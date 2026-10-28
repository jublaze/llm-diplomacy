# Report viewer package

This package is paired with the framework report-bundle schema. It intentionally excludes generated reports and historical/private fixtures. Supported Node releases are 22.12 or later within 22.x, 24.x, and 26 or later. Install the locked dependencies without running npm lifecycle hooks, then run the portable checks:

```powershell
npm ci --ignore-scripts
npm run typecheck
npm test
npm run build
```

Portable tests use explicit synthetic reports to check schema and semantic validation, catalog loading, and year navigation through 1912. Historical fixture regressions remain available through `npm run test:legacy` after approved fixtures, their generated catalog, and the optional `docs/reproducibility/v1/` materials have been supplied. Those materials describe fixture-linked workflows and are omitted from this source package because their sample report data is excluded. They are not part of clean-checkout checks. Use only approved, redistributable report bundles for viewer QA.
