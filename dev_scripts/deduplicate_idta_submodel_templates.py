"""
Remove redundant entities from the IDTA submodel templates.

This is important when multiple submodel templates are joined in a single merged
AAS environment -- each identifiable should appear only once.
"""

import json
import os.path
import pathlib
import sys
from typing import Set

import aas_core3.jsonization as aas_jsonization
import aas_core3.types as aas_types


def main() -> int:
    """Execute the main routine."""
    repo_root = pathlib.Path(os.path.realpath(__file__)).parent.parent
    originals_dir = repo_root / "test_data/idta_submodel_templates/originals"
    out_dir = repo_root / "test_data/idta_submodel_templates/deduplicated"

    paths = sorted(originals_dir.glob("**/*.json"))

    # NOTE (mristin):
    # We move all the concept descriptions to a separate file. If there are duplicates,
    # we simply pick the first.
    observed_concept_description_ids: Set[str] = set()

    environment_with_concept_descriptions = aas_types.Environment()

    out_dir.mkdir(parents=True, exist_ok=True)

    for path in paths:
        try:
            jsonable = json.loads(path.read_text(encoding="utf-8"))
            environment = aas_jsonization.environment_from_jsonable(jsonable)
        except Exception as exception:
            print(f"Failed to parse {path}: {exception}", file=sys.stderr)
            return 1

        for concept_description in environment.over_concept_descriptions_or_empty():
            if concept_description.id in observed_concept_description_ids:
                continue

            if environment_with_concept_descriptions.concept_descriptions is None:
                environment_with_concept_descriptions.concept_descriptions = []

            environment_with_concept_descriptions.concept_descriptions.append(
                concept_description
            )
            observed_concept_description_ids.add(concept_description.id)

        environment.concept_descriptions = None

        target_path = out_dir / (path.relative_to(originals_dir))
        target_path.write_text(
            json.dumps(
                aas_jsonization.to_jsonable(environment), indent=2, sort_keys=True
            ),
            encoding="utf-8",
        )
        print(f"Written to: {target_path}")

    concept_descriptions_path = out_dir / "environment_with_concept_descriptions.json"
    concept_descriptions_path.write_text(
        json.dumps(
            aas_jsonization.to_jsonable(environment_with_concept_descriptions),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(f"Written to: {concept_descriptions_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
