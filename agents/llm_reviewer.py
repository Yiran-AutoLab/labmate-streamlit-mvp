from __future__ import annotations

import pandas as pd

from agents.llm_client import chat_text


def review_layout(
    protocol: str,
    plate_layout: pd.DataFrame,
    well_contents: pd.DataFrame,
    *,
    provider: str,
    model: str,
    api_key: str,
) -> str:
    return chat_text(
        provider=provider,
        model=model,
        api_key=api_key,
        system_prompt="You review lab automation plate layouts. Be concise and point out inconsistencies.",
        user_prompt=(
            "Original protocol:\n"
            f"{protocol}\n\n"
            "Plate layout CSV:\n"
            f"{plate_layout.to_csv(index=False)}\n\n"
            "Well contents CSV:\n"
            f"{well_contents.to_csv(index=False)}"
        ),
    )


def mock_review_layout(protocol: str, plate_layout: pd.DataFrame, well_contents: pd.DataFrame) -> str:
    reaction_count = len(plate_layout)
    unique_templates = sorted(set(plate_layout.get("sample", [])))
    unique_conditions = sorted(set(plate_layout.get("condition", [])))
    total_by_well = well_contents.groupby("well")["volume_ul"].sum() if not well_contents.empty else {}
    bad_totals = [well for well, total in total_by_well.items() if abs(float(total) - 20.0) > 1e-6]
    lines = [
        "Mock AI review:",
        f"- Draft contains {reaction_count} destination wells.",
        f"- Samples detected: {', '.join(unique_templates[:6])}{'...' if len(unique_templates) > 6 else ''}.",
        f"- Conditions detected: {', '.join(unique_conditions)}.",
    ]
    if "T1-T6" in protocol and "P1 and P2" in protocol:
        lines.append("- Protocol rule T1-T6 with P1/P2 appears represented by the mock generator.")
    if "T7-T12" in protocol and "P1-P4" in protocol:
        lines.append("- Protocol rule T7-T12 with P1-P4 appears represented by the mock generator.")
    if bad_totals:
        lines.append(f"- Warning: {len(bad_totals)} wells do not total 20 uL.")
    else:
        lines.append("- All generated wells total 20 uL.")
    return "\n".join(lines)
