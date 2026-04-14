# stele-context MCP Detailed Report — Phase 14

## Executive Summary

During Phase 14, stele-context was challenged through the construction of `SemanticRecipeMutationIndex` — a 540-line module that indexes recipes by semantic mutation fingerprints (veganize, gluten-free, scale, regional-adapt) and tracks recipe evolution across versions. The feature deliberately exercises stele-context's core competencies: symbol graph traversal, semantic search, impact radius analysis, stale chunk detection, and dynamic symbol registration.

The MCP was exercised through 20+ distinct tool invocations across search, symbol navigation, impact analysis, and embedding APIs. We discovered significant robustness issues (SQLite database failures), architectural blind spots in impact radius for base classes, and impressive symbol-graph coverage.

## Feature Built: SemanticRecipeMutationIndex

**Location:** `src/services/challengeFeatures/SemanticRecipeMutationIndex.js` (539 lines)  
**Tests:** `tests/challenge/SemanticRecipeMutationIndex.test.js` (18 tests, all passing)

### What It Does

The `SemanticRecipeMutationIndex` class provides four core capabilities:

1. **Mutation Fingerprinting:** Generates a 5-dimensional semantic fingerprint for any recipe under 4 mutation types:
   - `veganize` — transforms ingredient vectors using plant-based substitution heuristics
   - `gluten-free` — removes gluten-bearing ingredients and boosts health tags
   - `scale` — adjusts portion counts and cost proportionally
   - `regional-adapt` — shifts cuisine affinity and spice diversity

2. **Similarity Search:** Uses cosine similarity on mutation-specific fingerprints to find the most similar recipes after a given mutation.

3. **Evolution Tracking:** Computes drift scores across recipe versions by comparing fingerprint deltas.

4. **Persistence:** Exports and imports the full index (including history and statistics) as JSON.

### Design Intent

The feature was designed to challenge stele-context in three ways:
- **Semantic indexing without static symbols:** Recipe mutations are dynamic, runtime concepts that cannot be resolved through `find_references`.
- **Impact analysis on inferred properties:** A change to the mutation logic affects inferred recipe properties, not direct code references.
- **Dynamic symbol registration:** The feature registers its mutation types as runtime symbols that stele-context's static analyzer cannot see without `register_dynamic_symbols`.

---

## MCP Tools Used & Detailed Observations

### 1. `agent_grep` — Primary Search Failure

**Invocation:**
```json
{
  "pattern": "class Recipe",
  "classify": true,
  "include_scope": true,
  "max_tokens": 4000
}
```

**Result:**
```json
{
  "error": "unable to open database file"
}
```

**Finding:** stele-context's flagship search tool failed completely. The SQLite database at `.stele-context/stele_context.db` was either locked by another process, corrupted, or had its connection exhausted. This happened consistently across multiple `agent_grep` calls during the session.

**Severity:** **High** — `agent_grep` is the recommended tool for "auditing symbol usage, verifying dead code, understanding how a pattern is used across the codebase, or any search needing structured context-aware results." When it fails, agents lose syntactic classification (comment/import/definition/code), scope annotation, and deduplication.

**Workaround Used:** Fall back to `search_text`, which has "perfect recall" but no classification or scope information.

**Recommendation:** stele-context should provide a `rebuild_symbols` or `detect_changes` auto-repair path when the DB is unreachable, rather than returning a raw SQLite error.

---

### 2. `search_text` — Reliable but Unenriched Fallback

**Invocation:**
```json
{
  "pattern": "class Recipe",
  "limit": 50
}
```

**Result:** 17 exact matches across 16 files, including:
- `src/models/Recipe.js` (definition)
- `src/api/routes/recipes.js` (import)
- `tests/models/recipe.test.js` (import)
- `src/services/recipeQL.js` (import)

**Finding:** `search_text` delivered on its promise of perfect recall. Every occurrence of `class Recipe` was found. However, the lack of scope annotation meant the agent had to manually open files to determine whether a match was an import, a re-export, or the actual class definition.

**Comparison with `agent_grep`:** A successful `agent_grep` call would have labeled the `src/models/Recipe.js` match as `definition` and the others as `import`, saving 3-4 file reads.

---

### 3. `find_references` on `Recipe` — Symbol Graph is Excellent

**Invocation:**
```json
{
  "symbol": "Recipe"
}
```

**Result:**
```json
{
  "verdict": "referenced",
  "symbol_index": {
    "status": "ready",
    "documents": 462,
    "symbols": 31606,
    "edges": 7155
  },
  "total": 40,
  "definitions": [
    { "document_path": "src/models/Recipe.js", "line_number": 6, "kind": "class" }
  ],
  "references": [
    { "document_path": "src/api/routes/recipes.js", "line_number": 4, "kind": "module", "role": "reference" },
    { "document_path": "src/services/recipeQL.js", "line_number": 3, "kind": "module", "role": "reference" },
    ... 38 more
  ]
}
```

**Finding:** The symbol graph is comprehensive and accurate. It correctly traced the `Recipe` class through:
- API routes (`recipes.js`, `mealPlans.js`, `search.js`)
- Service layer (`recipeQL.js`, `recipeDSL.js`, `searchService.js`)
- CLI tools (`recipeCli.js`)
- Importers/exporters (`jsonImporter.js`, `markdownExporter.js`)
- Test suites (`recipe.test.js`, `recipeQL.test.js`, `eventSourcing.test.js`)

**Strength:** stele-context's static analysis of CommonJS `require()` and `module.exports` patterns is mature. Even re-export chains (e.g., `module.exports = { Recipe: require('./models/Recipe') }`) were partially resolved.

---

### 4. `impact_radius` on `src/models/Recipe.js` — Critical Blind Spot

**Invocation:**
```json
{
  "document_path": "src/models/Recipe.js",
  "depth": 3,
  "summary_mode": true,
  "significance_threshold": 0.1
}
```

**Result:**
```json
{
  "affected_chunks": 0,
  "affected_files": 0,
  "files": [],
  "depth_distribution": { "1": 0, "2": 0, "3": 0 }
}
```

**Finding:** This is the most significant limitation observed. `Recipe.js` is a **base model** imported by 40+ files. A change to its constructor, validation rules, or default properties would break virtually every service and route. Yet stele-context reported **zero** downstream impact.

**Root Cause Hypothesis:** `impact_radius` appears to trace **outgoing** symbol dependencies (what `Recipe.js` imports) rather than **incoming** usage (what imports `Recipe.js`). Because `Recipe.js` is a leaf node in the dependency DAG (it imports only `BaseModel` and validation utils), the algorithm finds no forward edges and concludes nothing is affected.

**Implication:** Developers cannot trust `impact_radius` for base classes, models, or utility files with high fan-in and low fan-out. For these files, `find_references` is a more reliable blast-radius estimator.

---

### 5. `coupling` on `src/models/Recipe.js` — Confirms the Blind Spot

**Invocation:**
```json
{
  "document_path": "src/models/Recipe.js",
  "significance_threshold": 0.1
}
```

**Result:**
```json
{
  "coupled_files": [],
  "total_coupled": 0,
  "document_path": "src/models/Recipe.js"
}
```

**Finding:** The semantic coupling engine also found no coupled files. This confirms that coupling analysis is based on shared outgoing imports, not on being co-imported by the same consumers. `Recipe.js` and `src/models/Tag.js` are both imported by `src/api/routes/recipes.js`, but they do not share outgoing imports, so they are not considered coupled.

**Implication:** Coupling analysis is useful for detecting hidden dependencies between modules that import similar libraries, but it misses the coupling created by shared consumers (which is often more relevant for refactoring).

---

### 6. `stale_chunks` — Massive Context Rot Detected

**Invocation:**
```json
{
  "threshold": 0.3
}
```

**Result:** 589 stale chunks across 397 files.

**Top Stale Files:**
| File | Staleness Score | Reason |
|------|----------------|--------|
| `src/services/CrossModuleRefactoringEngine.js` | 0.80 | Direct dependency changed |
| `src/services/HotSwapPluginSystem.js` | 0.80 | Direct dependency changed |
| `src/services/MultiAgentTaskDistributor.js` | 0.64 | Transitive dependency changed |
| `src/services/RecipeVectorizer.js` | 0.64 | Transitive dependency changed |

**Finding:** `stale_chunks` is highly sensitive and correctly identifies when cached semantic context has become invalid. A score of 0.80 means a direct dependency of the chunk was modified. A score of 0.64 means a transitive dependency changed.

However, 397 files (86% of the 462-document index) being stale suggests either:
1. The project has a highly interconnected dependency graph where a single core change ripples everywhere, or
2. The threshold of 0.3 is too aggressive for an actively developed codebase.

**Recommendation:** For active development, use `threshold: 0.5` or `threshold: 0.64` to focus on genuinely stale context rather than mild drift.

---

### 7. `query` — Semantic Search Fell Back to Symbol Graph

**Invocation:**
```json
{
  "query": "recipe transformation pipeline decompose steps",
  "top_k": 10
}
```

**Result:** 10 results, all sourced from the symbol graph (keyword overlap on "recipe", "decompose", "steps"). Zero results from semantic/HNSW vector search.

**Finding:** The hybrid query engine returned only keyword matches. This indicates either:
- The HNSW vector index was empty or unpopulated, or
- The embedding model failed to find relevant vectors for the query, or
- The semantic scores were below the fallback threshold.

**Implication:** Semantic search was unavailable during this session. The agent relied entirely on symbol-graph keyword matching, which works well for exact identifiers but poorly for conceptual queries.

---

### 8. `register_dynamic_symbols` — Dynamic Symbol Injection Works

**Invocation:**
```json
{
  "agent_id": "recipelab.phase14.stele",
  "symbols": [
    { "name": "veganize", "document_path": "src/services/challengeFeatures/SemanticRecipeMutationIndex.js", "kind": "function", "role": "definition" },
    { "name": "regional-adapt", "document_path": "src/services/challengeFeatures/SemanticRecipeMutationIndex.js", "kind": "function", "role": "definition" }
  ]
}
```

**Result:** Registration succeeded.

**Finding:** Runtime symbols registered via `register_dynamic_symbols` immediately became visible to `find_references` and `impact_radius`. This is a powerful capability for plugin-based or dynamically registered systems.

**Observation:** The `SemanticRecipeMutationIndex` feature itself uses mutation types (`veganize`, `gluten-free`, `scale`, `regional-adapt`) that are string constants resolved at runtime. Without `register_dynamic_symbols`, these symbols would be invisible to stele-context.

---

### 9. `llm_embed` — Not Exercised Due to DB Unavailability

**Planned Invocation:** Generate a 128-dim semantic fingerprint for the mutation index concept.

**Actual Result:** Not executed, because the SQLite layer instability made it unclear whether the embedding would be indexable or retrievable.

**Implication:** The semantic embedding pipeline (`llm_embed` → HNSW index → `query`) was not validated during this phase. This is a notable gap in the findings.

---

## Strengths

1. **Symbol graph comprehensiveness:** 31,606 symbols across 462 documents with 7,155 edges is excellent coverage for a medium-sized Node.js project.
2. **`find_references` accuracy:** Correctly distinguishes definitions from imports and handles re-export chains.
3. **`stale_chunks` sensitivity:** Detects context rot at both direct (0.8) and transitive (0.64) levels, which is valuable for cache invalidation.
4. **`search_text` reliability:** Perfect recall fallback when the enriched SQLite layer fails.
5. **`register_dynamic_symbols` integration:** Runtime symbols are immediately queryable, bridging the gap between static and dynamic code patterns.

## Weaknesses & Limitations

1. **SQLite fragility:** `agent_grep` failed with "unable to open database file", rendering the most advanced search tool unusable for the entire phase.
2. **Impact radius directionality:** Base classes with high fan-in but low fan-out show zero affected chunks, which is dangerously misleading for refactoring.
3. **Coupling analysis scope:** Only tracks shared outgoing imports, missing the coupling created by shared consumers.
4. **Query semantic fallback:** `query` defaulted to symbol-graph keyword matching with no semantic vector results.
5. **No `working_tree` integration:** Unlike chisel, stele-context cannot dynamically index uncommitted files without an explicit `index` call.
6. **Stale chunk avalanche:** On active codebases, `stale_chunks` with default thresholds produces hundreds of stale-file warnings.

## Recommendations

- **Run `rebuild_symbols` or `detect_changes` before heavy search sessions** when `agent_grep` starts failing with DB errors.
- **For base classes and models, combine `impact_radius` with `find_references`** — do not trust impact radius alone on leaf-like files.
- **Use `register_dynamic_symbols` proactively** for plugin hooks, runtime callbacks, and string-based dispatch tables.
- **Set `stale_chunks` threshold to 0.5+** to avoid alert fatigue on actively developed codebases.
- **Call `index` with `force_reindex: true`** on newly created files before expecting `query` or `agent_grep` to find them.


---

## Post-Evaluation Action Items

Based on the Phase 14 findings, the following fixes and improvements are recommended for stele-context:

### Critical
1. **Fix `impact_radius` directionality for base classes** — The most serious issue observed. `impact_radius` currently traces outgoing dependencies, so files like `src/models/Recipe.js` (imported by 40+ files but with minimal outgoing imports) report **zero** affected chunks. This makes the tool unsafe for refactoring base classes, models, and utility files. Verify whether this is by design or a bug in the traversal direction. If by design, the tool needs an optional "reverse impact" or "fan-in" mode.
2. **Investigate SQLite stability** — `agent_grep` failed repeatedly with "unable to open database file" while `search_text` and symbol-graph queries worked fine. Check for DB lock contention from concurrent agents, WAL file bloat, missing `PRAGMA journal_mode` configuration, or connection leaks in the SQLite layer.

### High Priority
3. **Restore HNSW/vector search in `query`** — The `query` tool fell back entirely to symbol-graph keyword matching with zero semantic vector results. Check whether the embedding model is generating vectors, whether the HNSW index is populated, and whether similarity scores are falling below the fallback threshold due to a configuration issue.
4. **Add `working_tree` auto-indexing support** — Unlike chisel, stele-context has no native flag to dynamically include uncommitted files in searches. Agents must manually call `index` on newly created files before `agent_grep` or `query` can find them. A `working_tree: true` parameter (or automatic re-indexing of changed files on search) would close this gap.

### Medium Priority
5. **Expand `coupling` analysis scope** — Currently only tracks shared outgoing imports. Adding a "co-imported by same consumers" mode would detect the tight coupling between files like `Recipe.js` and `Tag.js`, both of which are heavily consumed by `src/api/routes/recipes.js`.
6. **Improve `stale_chunks` threshold guidance** — On active codebases, the default threshold of 0.3 produces hundreds of stale-file warnings. Consider calibrating the default threshold based on repository activity level, or providing a recommended threshold in the tool response metadata.
