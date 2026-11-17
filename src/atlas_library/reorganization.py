"""Read-only proposal for organizing sourcebooks into subject-based library stacks.

This module NEVER moves or renames PDFs and NEVER updates the catalog or OCR
index. Exports are proposals, not approval to perform a relocation.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Maximum two folder levels below the selected physical root. The primary
# physical address is a subject; series, author and publisher stay in metadata
# and virtual shelves.
SUBJECT_FOLDERS = (
    "00 Accession Desk/Awaiting Classification",
    "00 Accession Desk/New Acquisitions",
    "00 Accession Desk/Duplicate & Edition Review",
    "01 Rulebook Stacks/Core & Variant Rules",
    "01 Rulebook Stacks/Classes, Species & Feats",
    "01 Rulebook Stacks/Epic-Level Play",
    "02 Arcane Collection/Spells & Spellcraft",
    "02 Arcane Collection/Magic Items & Equipment",
    "02 Arcane Collection/Alchemy & Crafting",
    "03 Bestiary/Monsters & Dragons",
    "03 Bestiary/NPCs & Villains",
    "03 Bestiary/Encounters & Bosses",
    "04 World Atlas/Campaign Settings",
    "04 World Atlas/Planes & Cosmology",
    "04 World Atlas/Cultures, Factions & Lore",
    "05 Adventure Archives/Adventures & Modules",
    "05 Adventure Archives/Dungeons & Locations",
    "05 Adventure Archives/Maps & Handouts",
    "06 Campaign Systems/Bastions, Kingdoms & Economy",
    "06 Campaign Systems/Warfare, Naval & Vehicles",
    "06 Campaign Systems/Exploration & Survival",
    "06 Campaign Systems/Followers & Mounts",
    "07 Reference Desk/GM Guides & Game Design",
    "07 Reference Desk/Tables & Generators",
    "07 Reference Desk/Sheets & Play Aids",
)
AWAITING = SUBJECT_FOLDERS[0]


@dataclass(frozen=True)
class Rule:
    folder: str
    pattern: str
    reason: str


# Prefer subject-specific phrases to broad keywords. These are explicitly
# title-based suggestions and cannot substitute for reviewing each book.
RULES = (
    Rule("04 World Atlas/Cultures, Factions & Lore",
         r"\bkobold guide to worldbuilding\b",
         "Worldbuilding guide indicated in title"),
    Rule("03 Bestiary/Monsters & Dragons",
         r"\bkobold guide to monsters\b",
         "Creature reference indicated in title"),
    Rule("02 Arcane Collection/Spells & Spellcraft",
         r"\bkobold guide to magic\b",
         "Magic-focused reference indicated in title"),
    Rule("07 Reference Desk/Sheets & Play Aids",
         r"\b(character sheets?|pregenerated characters?|gm screen|"
         r"quick reference|play aids?|reference sheets?|fillable)\b",
         "Worksheet, screen or table-use aid indicated in title"),
    Rule("07 Reference Desk/GM Guides & Game Design",
         r"\b(game design|game mastering|game master guide|gamemaster guide|"
         r"complete kobold guide|kobold guide to)\b",
         "Game-design or GM reference indicated in title"),
    Rule("05 Adventure Archives/Maps & Handouts",
         r"\b(battle maps?|cartography|printable maps?|map pack|"
         r"maps and handouts|loremasters guide maps)\b|(?:^| )map(?:$| )",
         "Map or handout indicated in title"),
    Rule("03 Bestiary/Encounters & Bosses",
         r"\b(bloodied and bruised|final bosses|legendary foes|boss encounters?|"
         r"mentors and rivals|encounter builder)\b",
         "Encounter or boss reference indicated in title"),
    Rule("03 Bestiary/NPCs & Villains",
         r"\b(npcs?|villains?|non player characters?|people of the realm)\b",
         "NPC or villain reference indicated in title"),
    Rule("06 Campaign Systems/Warfare, Naval & Vehicles",
         r"\b(warfare|mass combat|naval|siege|sieges|battlefields|"
         r"vehicles?|mechs?|airships?|dreadnoughts?|war machine|"
         r"kingdoms and warfare)\b",
         "Warfare, vehicles or naval system indicated in title"),
    Rule("06 Campaign Systems/Bastions, Kingdoms & Economy",
         r"\b(bastions?|infrastructure|kingdom building|"
         r"strongholds?|settlements?|empire management|commerce|economy)\b",
         "Settlement, realm or economic system indicated in title"),
    Rule("06 Campaign Systems/Exploration & Survival",
         r"\b(survival|exploration|darker dungeons|grimdark|"
         r"gritty realistic|overland travel|wilderness rules)\b",
         "Travel, exploration or survival mechanics indicated in title"),
    Rule("06 Campaign Systems/Followers & Mounts",
         r"\b(companions?|followers?|pets?|mounts?|hirelings?|"
         r"retainers?|stibbles)\b",
         "Followers or companion rules indicated in title"),
    Rule("03 Bestiary/Monsters & Dragons",
         r"\b(bestiary|monster manual|monsters of the multiverse|"
         r"monsters?|draconomicon|monsternomicon|draconic creatures|"
         r"dragons bestiary|book of dragons|field guide.*inhuman|"
         r"field guide.*fickle|dinosaurs|tome of titans)\b",
         "Creature or dragon reference indicated in title"),
    Rule("01 Rulebook Stacks/Epic-Level Play",
         r"\b(epic legacy|epic heroes encyclopedia|"
         r"epic levels?|epic level handbook|epic level guide|"
         r"twentieth level|20th level character)\b",
         "Epic-level game material indicated in title"),
    Rule("01 Rulebook Stacks/Classes, Species & Feats",
         r"\b(feats?|species|subclasses?|class options?|ancestr(?:y|ies)|"
         r"character options?|archetypes?|barbarian|bard|cleric|druid|"
         r"fighter|monk|paladin|ranger|rogue|sorcerer|warlock|wizard|"
         r"racial traits?|mousefolk|slime folk|lamia|holstaur|"
         r"caprine|centaurs?|harpies|lizardmen|feline|lapine|"
         r"lacewing|arachne|deepkin|headless|wolf folk)\b",
         "Playable character choices indicated in title"),
    Rule("01 Rulebook Stacks/Core & Variant Rules",
         r"\b(player.?s handbook|dungeon masters? guide|"
         r"basic rules|core rulebook|rulebook|rules compendium|"
         r"game rules|variant rules)\b",
         "General rulebook indicated in title"),
    Rule("02 Arcane Collection/Alchemy & Crafting",
         r"\b(alchemy|alchemical|crafting|harvesting|"
         r"enchanting|enchantment crafting)\b",
         "Crafting, harvesting or alchemy indicated in title"),
    Rule("02 Arcane Collection/Spells & Spellcraft",
         r"\b(spell lists?|spellcasting|spellcraft|"
         r"grimoire|spell saying|new spells)\b",
         "Spells or spellcraft indicated in title"),
    Rule("02 Arcane Collection/Magic Items & Equipment",
         r"\b(magic items?|magical items?|fabled armory|armory|"
         r"arcanepunk arsenal|equipment|ascending items|artifacts?|"
         r"treasury of items|weapons and armor|loot tables)\b",
         "Equipment, magical items or treasure indicated in title"),
    Rule("04 World Atlas/Planes & Cosmology",
         r"\b(planes?|cosmology|feywild|shadowfell|acheron|"
         r"elemental chaos|abyssal depths|outer planes|infinite planes)\b",
         "Plane or cosmological setting indicated in title"),
    Rule("04 World Atlas/Cultures, Factions & Lore",
         r"\b(factions?|cultures?|lore and history|worldbuilding|"
         r"taverns?|stores|guilds?|faiths? and pantheons)\b",
         "World lore, factions or cultural reference indicated in title"),
    Rule("04 World Atlas/Campaign Settings",
         r"\b(campaign settings?|setting guide|setting book|"
         r"dark matter chapter introductions|realm wanderer|"
         r"beast world|the faerie realms)\b",
         "Campaign-setting material indicated in title"),
    Rule("05 Adventure Archives/Dungeons & Locations",
         r"\b(dungeons?|lighthouse|stygian library|"
         r"vault of the dracolich|ruins of|temples? of)\b",
         "Dungeon or adventure location indicated in title"),
    Rule("05 Adventure Archives/Adventures & Modules",
         r"\b(adventures?|modules?|campaign adventure|"
         r"one shot|one-shot|quest|the thief of thay|"
         r"lost mine of phandelver)\b",
         "Adventure or module indicated in title"),
    Rule("07 Reference Desk/Tables & Generators",
         r"\b(random tables?|generators?|dice tables?|"
         r"encounter tables?|roll tables?)\b",
         "Random table or content generator indicated in title"),
)


# Legacy folders can help generate a *reviewable suggestion* when a title
# alone gives no meaningful subject. Never use publisher/author/series paths as
# classification targets.
LEGACY_TOPIC_HINTS = (
    ("bestiar", "03 Bestiary/Monsters & Dragons"),
    ("character_options", "01 Rulebook Stacks/Classes, Species & Feats"),
    ("magic_items", "02 Arcane Collection/Magic Items & Equipment"),
    ("epic_level", "01 Rulebook Stacks/Epic-Level Play"),
    ("warfare_naval", "06 Campaign Systems/Warfare, Naval & Vehicles"),
    ("optional_rules", "01 Rulebook Stacks/Core & Variant Rules"),
    ("settings_planes", "04 World Atlas/Campaign Settings"),
    ("adventures_modules", "05 Adventure Archives/Adventures & Modules"),
    ("battle_maps", "05 Adventure Archives/Maps & Handouts"),
)


def _title_for_classification(book: dict) -> str:
    # Publisher is intentionally not an input to classification.
    name = str(book.get("title") or Path(book["path"]).stem)
    display = str(book.get("display_title") or "")
    return " ".join((name, display)).casefold().replace("_", " ").replace("-", " ")


def suggest_folder(book: dict, root: Path) -> tuple[str, str, str, str]:
    """Return proposed folder, confidence, rationale and alternate subjects.

    No proposal is ever automatically approved.
    """
    title = _title_for_classification(book)
    matches = []
    for rule in RULES:
        if re.search(rule.pattern, title, flags=re.IGNORECASE):
            if rule.folder not in [previous.folder for previous in matches]:
                matches.append(rule)
    if matches:
        best = matches[0]
        alternatives = "; ".join(rule.folder for rule in matches[1:])
        confidence = "HIGH" if not alternatives else "REVIEW MULTI-TOPIC"
        return best.folder, confidence, best.reason, alternatives

    try:
        relative_parent = Path(book["path"]).relative_to(root).parent
        normalized_parent = str(relative_parent).casefold().replace(" ", "_")
        for needle, folder in LEGACY_TOPIC_HINTS:
            if needle in normalized_parent:
                return (
                    folder, "LOW (LEGACY FOLDER)",
                    "Inferred from existing subject folder, not the book contents",
                    "",
                )
    except ValueError:
        pass
    return AWAITING, "UNCLASSIFIED", "Title insufficient for subject assignment", ""


HEADERS = (
    "Review Status", "Source Path", "Current Relative Path",
    "Filename", "Display Title", "Authors", "Publisher", "Edition",
    "Game System", "Tags", "Virtual Shelves",
    "Proposed Subject Folder", "Proposed Relative Path",
    "Confidence", "Suggestion Reason", "Alternative Subjects",
    "Review Flags", "Source Size (Catalog Bytes)",
    "Source Modified (Catalog ns)", "Approved Subject Folder",
    "Reviewer Notes",
)


def _csv_safe(value: object) -> str:
    """Prevent Excel/LibreOffice from executing formula-looking book titles."""
    value = str(value if value is not None else "")
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def build_preview_rows(
    catalog, root: Path, books: Iterable[dict],
) -> tuple[list[dict], dict]:
    """Use current catalog entries; only filesystem read is stat() where safe."""
    root = Path(root).resolve()
    shelves = {entry["id"]: entry["name"] for entry in catalog.list_collections()}
    results = []
    proposed_targets: dict[str, list[int]] = {}

    for book in books:
        source = Path(book["path"])
        try:
            relative = source.relative_to(root)
        except ValueError:
            # Mark cross-root/stale records, never propose moving them.
            relative = Path(source.name)
            outside_root = True
        else:
            outside_root = False

        subject, confidence, reason, alternatives = suggest_folder(book, root)
        if outside_root:
            subject, confidence, reason, alternatives = (
                AWAITING, "UNCLASSIFIED", "Catalog path is outside selected root", "",
            )
        destination = (Path(subject) / source.name).as_posix()

        metadata = catalog.get_metadata(str(source))
        shelf_names = [
            shelves[identifier] for identifier in sorted(
                catalog.get_book_collections(str(source))
            ) if identifier in shelves
        ]
        flags = []
        if outside_root:
            flags.append("OUTSIDE_LIBRARY_ROOT")
        try:
            stat = source.stat()
        except OSError:
            flags.append("SOURCE_MISSING_OR_UNREADABLE")
        else:
            if (
                stat.st_size != int(book["size_bytes"])
                or stat.st_mtime_ns != int(book["modified_ns"])
            ):
                flags.append("CHANGED_SINCE_CATALOG_SCAN")
        if subject == AWAITING:
            flags.append("NEEDS_SUBJECT_REVIEW")
        if alternatives:
            flags.append("MULTIPLE_POSSIBLE_SUBJECTS")

        target_key = destination.casefold()
        proposed_targets.setdefault(target_key, []).append(len(results))
        results.append({
            "Review Status": "PENDING",
            "Source Path": str(source),
            "Current Relative Path": relative.as_posix(),
            "Filename": source.name,
            "Display Title": metadata.get("display_title") or book["title"],
            "Authors": metadata.get("authors", ""),
            "Publisher": metadata.get("publisher", ""),
            "Edition": metadata.get("edition", ""),
            "Game System": metadata.get("game_system", ""),
            "Tags": "; ".join(metadata.get("tags", [])),
            "Virtual Shelves": "; ".join(shelf_names),
            "Proposed Subject Folder": subject,
            "Proposed Relative Path": destination,
            "Confidence": confidence,
            "Suggestion Reason": reason,
            "Alternative Subjects": alternatives,
            "Review Flags": flags,
            "Source Size (Catalog Bytes)": str(book["size_bytes"]),
            "Source Modified (Catalog ns)": str(book["modified_ns"]),
            "Approved Subject Folder": "",
            "Reviewer Notes": "",
        })

    for matching in proposed_targets.values():
        if len(matching) > 1:
            for index in matching:
                results[index]["Review Flags"].append("TARGET_NAME_COLLISION")

    for row in results:
        row["Review Flags"] = "; ".join(row["Review Flags"])

    summary = {
        "total": len(results),
        "proposed": sum(r["Proposed Subject Folder"] != AWAITING for r in results),
        "unclassified": sum(r["Proposed Subject Folder"] == AWAITING for r in results),
        "multi_topic": sum("MULTIPLE_POSSIBLE_SUBJECTS" in r["Review Flags"] for r in results),
        "collisions": sum("TARGET_NAME_COLLISION" in r["Review Flags"] for r in results),
        "missing": sum("SOURCE_MISSING_OR_UNREADABLE" in r["Review Flags"] for r in results),
    }
    return results, summary


def export_preview_csv(destination: Path, rows: list[dict]) -> None:
    """Atomically create a review-only CSV, never touching any source PDF."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="",
            prefix=".atlas-preview-", suffix=".tmp",
            dir=str(destination.parent), delete=False,
        ) as output:
            temp_name = output.name
            writer = csv.DictWriter(output, fieldnames=HEADERS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({heading: _csv_safe(row.get(heading, "")) for heading in HEADERS})
        os.replace(temp_name, destination)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
