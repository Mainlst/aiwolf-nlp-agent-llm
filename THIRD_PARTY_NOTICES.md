# Attribution and scope

The agent code is derived from `aiwolfdial/aiwolf-nlp-agent-llm`. Preserve the MIT license with its **Copyright (c) 2024 Kano Laboratory** notice in all code redistributions. The project metadata retain the upstream authors and maintainers; paper author metadata are recorded separately in `CITATION.cff`.

The full manuscript and its ACL style files are not bundled in this code release. Minimal historical excerpts document case-selection provenance in `docs/provenance/`; their inclusion does not set the manuscript's publication license. Third-party Python packages are installed separately; see `uv.lock` for the full environment and their respective distributions for licenses.

The game server binary is not bundled. Recovered historical server source is documented under `experiments/pp/revision/server/`; its separate MIT notice is [source/LICENSE](experiments/pp/revision/server/source/LICENSE), with `Copyright (c) 2024 Yuto Sahashi (Kano Laboratory)`. Preserve that notice when redistributing the server source. Historical server identifiers remain recorded in the saved run manifests.

The dataset's new research materials are distributed under [dataset/LICENSE](dataset/LICENSE), with the research authors' 2026 MIT notice. Bundled upstream agent-code snapshots retain their original 2024 Kano Laboratory MIT notice in [dataset/CODE-LICENSE-MIT](dataset/CODE-LICENSE-MIT). See [dataset/LICENSE-SCOPE.md](dataset/LICENSE-SCOPE.md) for the distinction and the limits concerning third-party rights. Neither license assigns rights to third-party model services or changes the manuscript's publication license.
