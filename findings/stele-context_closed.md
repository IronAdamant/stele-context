# Stele-context MCP Findings — Review Eleven

**Date:** 2026-04-10
**Features Tested Against:** SemanticCodeNavigator (primary), RecipeLineageTracker, AdaptiveRecipeCompiler, all 6 features

## Executive Summary

Stele-context was challenged primarily by the **SemanticCodeNavigator** feature — a service built around heavily aliased symbols, proxy patterns, re-exports under different names, and dynamic method dispatch. This directly tests Stele's `find_references` and `find_definition` capabilities against indirected symbols. Stele showed **strong performance on direct symbols and import tracking** but **completely failed to resolve re-exported aliases** — the core challenge.

## Tools Tested

### 1. `index` — PASSED
- **Result:** Successfully indexed all 30 new files (18 source + 6 routes + 6 tests).
- **Stats:** 51 chunks, 48,483 tokens total.
- **Observation:** Fast and reliable. Chunking was appropriate — smaller files got 1-2 chunks, larger files got 2-3.
- **Verdict:** Continues to be Stele's most reliable feature.

### 2. `detect_changes` — PASSED
- **Result:** Correctly identified 1 modified file (`routeLoader.js`) and 160+ new files (including all 6 feature directories).
- **Key observation:** `scan_new: true` (default) correctly discovered all new files by scanning the filesystem. This addresses the previous limitation where `detect_changes` couldn't find unindexed files.
- **Verdict:** Working well with the filesystem scan mode.

### 3. `find_references` — MIXED RESULTS

#### Test 1: `SemanticCodeNavigatorService` (canonical name)
- **Verdict:** `external` (only 1 reference found)
- **Found:** Import in `semanticNavigatorRoutes.js` (line 5)
- **Missing:** Did NOT find the class definition in `SemanticCodeNavigatorService.js`, did NOT find usage in `semanticNavigator.test.js`
- **Assessment:** PARTIAL FAILURE. The class is defined and imported in multiple files but Stele only found one reference.

#### Test 2: `CodeNavigator` (alias of SemanticCodeNavigatorService)
- **Verdict:** `not_found`
- **Result:** Zero matches. The symbol `CodeNavigator` exists as `const CodeNavigator = SemanticCodeNavigatorService` in the service file and is imported in the test file.
- **Assessment:** COMPLETE FAILURE. Stele cannot resolve `const Alias = OriginalClass` patterns. This is the core challenge gap.

#### Test 3: `RecipeSnapshot` (alias of createLineageNode)
- **Verdict:** `referenced` (2 matches — 1 definition, 1 import)
- **Found:** Variable definition in `LineageNode.js` (line 103), import in `lineageTracker.test.js` (line 4)
- **Assessment:** PARTIAL SUCCESS. Found the alias definition and its test import. But missed the module.exports line where it's re-exported.

#### Test 4: `DynamicDispatcher` (class definition)
- **find_definition result:** Zero definitions found.
- **Assessment:** COMPLETE FAILURE. `DynamicDispatcher` is a `class` defined in `DynamicDispatcher.js` and exported via `module.exports`. Stele's symbol extractor missed it entirely.
- **Possible cause:** The class is exported as `module.exports = { DynamicDispatcher }` (destructured export), and the symbol extractor may not parse class definitions inside destructured exports.

### 4. `coupling` — STRONG PERFORMANCE
- **File tested:** `src/services/lineageTracker/RecipeLineageTrackerService.js`
- **Result:** 65 coupled files found with detailed direction and shared symbols.
- **Top couplings:**
  - `lineageTracker.test.js` — 14 shared symbols, bidirectional
  - `lineageRoutes.js` — 10 shared symbols, `depended_on_by`
  - `LineageGraph.js` — 8 shared symbols, bidirectional (actual import dependency)
- **Assessment:** EXCELLENT. Stele's semantic coupling correctly identified the real import dependencies (LineageGraph, LineageNode) AND the test/route files that consume the service. Direction labels are accurate.
- **Noise issue:** Many files coupled via generic symbols like `path` and `getStats`. These are false positives from method-name collisions, not actual coupling.

### 5. `search` (keyword mode) — GOOD RESULTS
- **Query:** "dynamic dispatch aliased symbols proxy pattern"
- **Mode:** `keyword` (BM25)
- **Top results:**
  1. `tests/services/semanticNavigator.test.js` — 0.893 relevance
  2. `src/services/semanticNavigator/SemanticCodeNavigatorService.js` — 0.825
  3. `src/services/semanticNavigator/SymbolAliasRegistry.js` — 0.648
  4. `src/services/semanticNavigator/DynamicDispatcher.js` — 0.624
- **Assessment:** SIGNIFICANT IMPROVEMENT. BM25 keyword search returned highly relevant results for this query. All top 5 results are from the correct feature. This confirms the Phase 9 finding that BM25 mode is far more useful than hybrid for specific queries.

## Challenge-Specific Findings

### Symbol Alias Resolution (Core Challenge)

| Symbol | Type | find_references | find_definition |
|---|---|---|---|
| `SemanticCodeNavigatorService` | class (canonical) | 1 ref (external) | Not tested |
| `CodeNavigator` | const alias | NOT FOUND | N/A |
| `SymbolNavigator` | const alias | Not tested | N/A |
| `RecipeSnapshot` | const alias | 2 refs (referenced) | N/A |
| `DerivationPoint` | const alias | Not tested | N/A |
| `DynamicDispatcher` | class (canonical) | Not tested | NOT FOUND |
| `StepParser` | const alias | Not tested | N/A |
| `RecipeDecoder` | const alias | Not tested | N/A |

**Pattern:** Stele fails to index:
1. **`const Alias = OriginalClass` patterns** — `CodeNavigator`, `SymbolNavigator`, `StepParser`, `RecipeDecoder` all invisible
2. **Class definitions in destructured exports** — `DynamicDispatcher` defined as `class DynamicDispatcher` but exported via `module.exports = { DynamicDispatcher }` was not found by `find_definition`
3. **Re-exported aliases in `module.exports`** — When `module.exports = { Original, Alias1: Original, Alias2: Original }`, the aliases are invisible

### Coupling Noise from Generic Symbols
Stele's coupling correctly found the real dependencies but also reported 40+ false-positive couplings through shared method names:
- `getStats` appears in nearly every service file → massive false coupling
- `path` (the Node.js module) creates coupling between every file that imports it

**Recommendation:** Coupling should filter out stdlib imports (`path`, `fs`, `crypto`) and extremely common method names (`getStats`, `constructor`, `toJSON`).

## Bugs / Issues Found

1. **`find_definition` failed for `DynamicDispatcher`** — Class exported via destructured `module.exports` not indexed
2. **`find_references` returns `external` for `SemanticCodeNavigatorService`** — Should be `referenced` since definition and imports both exist
3. **`const Alias = Class` pattern invisible** — Re-export aliases (`CodeNavigator`, `StepParser`, etc.) completely missing from symbol index
4. **Coupling noise from generic symbols** — `getStats` and `path` create massive false-positive coupling

## Validated Capabilities (confirmed working)

- [x] `index` — Fast, reliable, correct chunking
- [x] `detect_changes` with `scan_new: true` discovers new unindexed files
- [x] `search` in `keyword` mode returns highly relevant results (0.89 relevance)
- [x] `coupling` correctly identifies real import dependencies with direction
- [x] `find_references` for `RecipeSnapshot` (const alias) partially works (found definition + test import)

## Unresolved Issues (carried forward)

- [ ] **CRITICAL:** `const Alias = Class` patterns invisible to symbol index
- [ ] **CRITICAL:** `find_definition` misses classes in destructured `module.exports`
- [ ] `find_references` verdict inconsistency (`external` when definition exists in indexed files)
- [ ] Coupling polluted by stdlib imports and common method names — needs filtering
- [ ] `search` in `hybrid` mode still unreliable (not tested this round, but BM25 clearly superior)

---

## Part 2: Multi-Agent Refactor Findings

**Refactor scope:** 3 agents — Agent A (barrel modules), Agent B (VCS route merge), Agent C (BaseModel + FileStore).

### `index` post-refactor — PASSED
- Indexed 10 new/modified files: 5 barrel modules, vcsRoutes.js, BaseModel.js, fileStore.js, routeLoader.js, baseModel.test.js
- Total: 15 chunks, 13,972 tokens. Fast and clean.

### `find_references` for `BaseModel` — CORRECT
- **Verdict:** `referenced` (2 matches)
- Found: class definition in `src/models/BaseModel.js` (line 1), import in `tests/utils/baseModel.test.js` (line 7)
- **Assessment:** Correctly found the new class and its test consumer. Direction is right — BaseModel is defined once and imported once.
- **Contrast with Part 1:** `DynamicDispatcher` (also a class) was NOT found by `find_definition`. `BaseModel` WAS found. The difference: `BaseModel` uses `module.exports = { BaseModel }` just like `DynamicDispatcher`. But BaseModel's file is simpler (no other exports). This suggests the symbol extractor may have a bug with files that export many symbols.

### `find_references` for `bulkCreate` — EXCELLENT
- **Verdict:** `referenced` (6 matches)
- Found: 2 definitions (FileStore.js line 161, Ingredient.js line 80) + 4 references in baseModel.test.js
- **Assessment:** Outstanding. Stele found `bulkCreate` in TWO definition locations — the new FileStore method AND a pre-existing `bulkCreate` method on the Ingredient model. This cross-file definition tracking is exactly what Stele excels at.

### `find_definition` for `findWhere` — CORRECT
- Found in `src/utils/fileStore.js` at line 153 with full chunk content.
- **Assessment:** New methods added by Agent C are immediately visible to Stele after indexing.

### `coupling` for barrel module — EMPTY (0 coupled files)
- **File:** `src/services/mcpProbes/chiselProbes.js`
- **Result:** 0 coupled files
- **Assessment:** FAILURE. The barrel module re-exports from 3 other files via `require()`. Stele should show coupling to those 3 source files. Instead, it shows nothing.
- **Root cause hypothesis:** Barrel modules use `module.exports = { ...require('./a'), ...require('./b') }` spread syntax. Stele's symbol extractor likely doesn't resolve spread-imported symbols, so no shared symbols are detected.

### `coupling` for merged VCS routes — STRONG
- **File:** `src/api/routes/vcsRoutes.js`
- **Result:** 24 coupled files, top coupling:
  - `versionControlService.js` — 14 shared symbols (bidirectional)
  - `response.js` — 6 shared symbols (depends_on)
  - `diffService.js` — 4 shared symbols (depends_on)
  - `routeLoader.js` — 2 shared symbols, correctly shows `depended_on_by` direction
- **Assessment:** Excellent. Agent B merged 3 route files into one, and Stele's coupling correctly shows the new merged file's full dependency map. The `routeLoader.js` coupling with `createVcsRoutes` symbol proves Stele tracked the refactored import path.

### Multi-Agent Coordination Impact on Stele
- Agent A's barrel modules are invisible to coupling (spread syntax gap)
- Agent B's merged routes are fully visible with correct coupling
- Agent C's new FileStore methods are immediately findable after indexing
- **Cross-agent conflict detected:** Both the old `branchRoutes.js` and new `vcsRoutes.js` show coupling to `versionControlService.js`. Stele doesn't know the old files are superseded — it would flag both as coupled. This is a stale-data issue that `detect_changes` should help with.

### New issue found
- [ ] **Barrel module spread syntax** (`module.exports = { ...require('./x') }`) produces zero coupling — Stele cannot resolve spread re-exports
