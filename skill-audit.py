"""
Skill Audit: Detect overlap, drift, and dead skills.

Runs weekly (Sunday 8 AM) or on-demand. Scans all skill definitions,
finds duplicates by trigger pattern overlap, and flags skills that
haven't been used recently.

Output: audit report to stdout + alerts/skill-audit-{date}.md
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def load_skills(skill_dir: Path) -> list[dict]:
    """Load skill metadata from SKILL.md frontmatter."""
    skills = []
    for skill_file in sorted(skill_dir.rglob("SKILL.md")):
        content = skill_file.read_text()
        name = skill_file.parent.name

        # Extract triggers/description from frontmatter or content
        triggers = set()
        description = ""

        # Look for "Trigger on:" or "Triggers on:" patterns
        for line in content.splitlines():
            lower = line.lower()
            if "trigger" in lower and ":" in line:
                # Extract quoted trigger phrases
                phrases = re.findall(r'"([^"]+)"', line)
                triggers.update(p.lower().strip() for p in phrases)
                # Also extract slash commands
                slashes = re.findall(r'/[\w-]+', line)
                triggers.update(s.lower() for s in slashes)
            if line.startswith("description:"):
                description = line.split(":", 1)[1].strip().strip('"')

        # First non-empty non-frontmatter line as fallback description
        if not description:
            in_frontmatter = False
            for line in content.splitlines():
                if line.strip() == "---":
                    in_frontmatter = not in_frontmatter
                    continue
                if not in_frontmatter and line.strip() and not line.startswith("#"):
                    description = line.strip()[:100]
                    break

        skills.append({
            "name": name,
            "path": str(skill_file.relative_to(skill_dir.parent.parent)),
            "triggers": triggers,
            "description": description,
            "size": len(content),
        })

    return skills


def find_trigger_overlaps(skills: list[dict]) -> list[dict]:
    """Find skills with overlapping trigger phrases."""
    trigger_map = defaultdict(list)

    for skill in skills:
        for trigger in skill["triggers"]:
            # Normalize: strip slashes, lowercase
            normalized = trigger.strip("/").lower()
            if len(normalized) > 2:  # Skip tiny triggers
                trigger_map[normalized].append(skill["name"])

    overlaps = []
    for trigger, skill_names in trigger_map.items():
        if len(skill_names) > 1:
            overlaps.append({
                "trigger": trigger,
                "skills": sorted(set(skill_names)),
                "count": len(set(skill_names)),
            })

    return sorted(overlaps, key=lambda x: x["count"], reverse=True)


def check_usage_log() -> dict[str, int]:
    """Read skill usage log to find recently used skills."""
    log_file = Path(__file__).parent.parent.parent / ".claude" / "skill-usage-log.jsonl"

    if not log_file.exists():
        return {}

    usage_counts = defaultdict(int)
    try:
        for line in log_file.read_text().splitlines():
            if line.strip():
                try:
                    entry = json.loads(line)
                    skill_name = entry.get("skill", "")
                    if skill_name:
                        usage_counts[skill_name] += 1
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return dict(usage_counts)


def main():
    now = datetime.now(timezone.utc)
    workspace = Path(__file__).parent.parent.parent

    print(f"{'=' * 60}")
    print(f"  Skill Audit  |  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 60}")
    print()

    # Collect skills from all locations
    skill_dirs = [
        ("Project skills", workspace / "_context" / "skills"),
        ("User skills", Path.home() / ".claude" / "skills"),
    ]

    all_skills = []
    for label, skill_dir in skill_dirs:
        if skill_dir.exists():
            skills = load_skills(skill_dir)
            print(f"  {label}: {len(skills)} skills in {skill_dir}")
            all_skills.extend(skills)

    print(f"  Total: {len(all_skills)} skills")
    print()

    # Find overlaps
    overlaps = find_trigger_overlaps(all_skills)
    if overlaps:
        print(f"  Trigger Overlaps ({len(overlaps)} found):")
        for o in overlaps[:15]:
            print(f"    \"{o['trigger']}\" -> {', '.join(o['skills'])}")
        print()
    else:
        print("  No trigger overlaps found.")
        print()

    # Check usage
    usage = check_usage_log()
    if usage:
        used_names = set(usage.keys())
        all_names = set(s["name"] for s in all_skills)
        unused = all_names - used_names

        print(f"  Usage Stats:")
        print(f"    Skills with recorded usage: {len(used_names)}")
        print(f"    Skills never triggered: {len(unused)}")
        if unused:
            # Show top unused (by alphabetical, limit 20)
            print(f"    Unused (sample): {', '.join(sorted(unused)[:20])}")
        print()

        # Top used
        top_used = sorted(usage.items(), key=lambda x: x[1], reverse=True)[:10]
        if top_used:
            print(f"  Top 10 Most Used Skills:")
            for name, count in top_used:
                print(f"    {count:>4}x  {name}")
            print()
    else:
        print("  No usage log found (skill-usage-log.jsonl)")
        print()

    # Size analysis (find unusually large skills that might need splitting)
    large_skills = sorted(
        [s for s in all_skills if s["size"] > 5000],
        key=lambda x: x["size"],
        reverse=True,
    )
    if large_skills:
        print(f"  Large Skills (>5KB, may need splitting):")
        for s in large_skills[:10]:
            print(f"    {s['size']:>6} bytes  {s['name']}")
        print()

    # Write audit report
    report_dir = Path(__file__).parent / "alerts"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / f"skill-audit-{now.strftime('%Y-%m-%d')}.md"

    report_lines = [
        f"# Skill Audit Report: {now.strftime('%Y-%m-%d')}",
        f"",
        f"Total skills: {len(all_skills)}",
        f"Trigger overlaps: {len(overlaps)}",
        f"",
    ]

    if overlaps:
        report_lines.append("## Trigger Overlaps")
        for o in overlaps:
            report_lines.append(f"- **\"{o['trigger']}\"**: {', '.join(o['skills'])}")
        report_lines.append("")

    if usage:
        unused = set(s["name"] for s in all_skills) - set(usage.keys())
        if unused:
            report_lines.append(f"## Unused Skills ({len(unused)})")
            for name in sorted(unused):
                report_lines.append(f"- {name}")
            report_lines.append("")

    report_file.write_text("\n".join(report_lines))
    print(f"  Report: {report_file}")

    # Summary
    issues = len(overlaps) + len(large_skills)
    if issues > 0:
        print(f"\n  Audit: {issues} items to review")
    else:
        print(f"\n  Audit: Clean")


if __name__ == "__main__":
    main()
