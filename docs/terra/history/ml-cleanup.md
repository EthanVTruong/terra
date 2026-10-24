# ML functionality cleanup complete

Historical report from the original local Terra prototype. These statements
apply to that checkout; the shared ChillWatch model pipeline is unchanged by
the additive integration. Original source/data identities are retained below.

Terra now contains only the statistical residual detector and its supported
fitting/scoring/replay pipeline. The optional forest/challenger implementations,
legacy deserialization and conversion commands, optional dependency installer,
legacy-only tests and executable historical experiment wrappers are removed.

The project virtual environment no longer contains scikit-learn, SciPy, joblib,
threadpoolctl or cloudpickle. The orphaned cloudpickle lock pin is removed.
`pip check` reports no broken requirements. Historical report text and defensive
no-ML import/field guards remain; neither supplies ML execution functionality.

## Verification

- All200Python tests pass:31harness,154pipeline,15temporal. Useful coverage,
  boundary, persistence and scenario checks from retired tests are preserved in
  the statistical suite.
- The actual selected reference/data bytes are unchanged. Regenerating all25
  replay cases and2400hourly prefixes produces a byte-identical console packet.
- The frontend production build and4packaging tests pass. Its existing large
  bundle advisory remains. Browser comparison/evidence remains correct with no
  captured warning/error; viewport reset and replay paused.
- All503retired files and their archive entries were individually SHA-verified.
  Original weather records, SQLite, finite statistical JSON references, original
  PDF, current UI source and9stagedbootstrap files are preserved.

## Recoverable retirement

Automatic approval review blocked a bulk-deletion command before it ran. The
completed alternative uses a verified archive plus recoverable moves with a
`.retired` extension; no bulk deletion was retried. Obsolete source/model/cache
paths are absent from their runnable locations. Historical ML code, binaries and
scratch copies can be deliberately recovered from the ignored archive, and are
not part of the application, Python import path or supported commands.

Archive: `.terra-local/retired/ml-retirement-20261024T060526Z.zip`  
SHA-256: `8ce38ffdea3d0b732a32b9bfbc99cdf1aaf59cd14aa4cb1493eecdfb515c2b58`  
Entry manifest: `.terra-local/retired/ml-retirement-20261024T060526Z.json`  
Disabled recovery files: `.terra-local/retired/ml-disabled-20261024T060526Z/`

The archive contains code/model/cache files, not credentials or original weather
JSON/SQLite. Existing historical artifact metadata retains its original IDs and
hashes; the coordination record points retired binaries to their archive members.
Old benchmark reproduction commands are retired, as explained in the experiment
README. Factual numeric reports remain available.

## Current checkpoint

Reference: `terra-qc-v4-e1e59ac0ea395d80`  
Reference SHA-256: `809c78b69d80d93d73f5b4b75ca6e74b0759ddcebf1e17fa2450386b44fcd90f`  
Dataset SHA-256: `4d152b359ac00bc343137315eccbe77737d3a3324a363b5b7cae659e7f1cd741`  
Console SHA-256: `23a9a02e60a4b1cc5ae24b043c04978f24b247a21ddc52cd0562b4080b3d9aee`

Exact changed/removed paths, module hashes, model locations and verification are
in `ml-cleanup-results.json`. This checkout has no initial commit, remote or Git
author identity configured. The user-supplied team destination is
`https://github.com/EthanVTruong/terra`; the coordinating monitoring/integration
chat owns preparation against its actual main. This cleanup made no commit,
push, remote change, monitoring implementation or change to Nathan's ownership.
