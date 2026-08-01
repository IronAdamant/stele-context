# Stele-context Challenge Report — Phase 13

## Independence Manifesto

**Stele-context is a standalone MCP.** You do not need chisel, trammel, or coordinationhub to use it. It operates purely on its own semantic index and symbol graph, requires no external APIs, and maintains zero third-party dependencies. This report evaluates stele-context on its own merits—not as a link in a chain, but as a self-contained code-intelligence engine that you may choose to use alone or alongside other tools.

---

## Feature: DynamicSymbolMesh

**DynamicSymbolMesh** generates 60+ runtime symbols across 6 business domains (recipe, ingredient, mealPlan, shoppingList, collection, dietaryProfile) and registers them dynamically with stele-context. It then queries `find_references`, `impact_radius`, and `coupling` to validate how well the semantic engine handles dynamically registered symbols at scale.

---

## Symbol Generation Summary

- **Total Symbols Generated:** 60
- **Domains:** recipe (10 actions × 3 hooks), ingredient (10 actions × 3 hooks), mealPlan, shoppingList, collection, dietaryProfile
- **Symbols Registered:** 60
- **Embeddings Stored:** 0 (attempted but failed — see `llm_embed` entry below)

---

## MCP Call Log

### register_dynamic_symbols (2026-04-14T05:30:00Z)

**Params:**
```json
{
  "symbols": "Array(60)",
  "agent_id": "recipelab.phase13.parent"
}
```

**Response Summary:**
```json
{
  "stored": 60,
  "errors": "Array(0)"
}
```

**Analysis:** All 60 dynamic symbols were accepted in a single batch call. The stele-context symbol graph grew from 31,073 to 31,133 rows, confirming that runtime symbols are persisted alongside static ones.

---

### find_references — onRecipeCreate (2026-04-14T05:30:05Z)

**Params:**
```json
{ "symbol": "onRecipeCreate" }
```

**Response Summary:**
```json
{
  "symbol": "onRecipeCreate",
  "verdict": "unreferenced",
  "definitions": "Array(1)",
  "references": "Array(0)",
  "total": 1,
  "symbol_index": {
    "status": "ready",
    "indexed_documents": 460,
    "symbol_row_count": 31133,
    "edge_count": 7447
  }
}
```

**Analysis:** The symbol was found with a runtime chunk ID (`runtime:recipelab.phase13.parent:onRecipeCreate`). Verdict is `unreferenced` because no indexed file actually calls this dynamically registered hook. This is technically correct but highlights a limitation: dynamic symbols without real callers appear as dead code.

---

### find_references — beforeIngredientSubstitute (2026-04-14T05:30:06Z)

**Params:**
```json
{ "symbol": "beforeIngredientSubstitute" }
```

**Response Summary:**
```json
{
  "symbol": "beforeIngredientSubstitute",
  "verdict": "unreferenced",
  "definitions": "Array(1)",
  "references": "Array(0)",
  "total": 1,
  "symbol_index": {
    "status": "ready",
    "indexed_documents": 460,
    "symbol_row_count": 31133,
    "edge_count": 7447
  }
}
```

**Analysis:** Same pattern as above. The dynamic symbol is tracked but has no inbound references from the static codebase.

---

### impact_radius — onRecipeCreate.js (2026-04-14T05:30:10Z)

**Params:**
```json
{
  "document_path": "src/services/dynamicSymbolMesh/onRecipeCreate.js",
  "summary_mode": true,
  "top_n_files": 5
}
```

**Response Summary:**
```json
{
  "origin": "src/services/dynamicSymbolMesh/onRecipeCreate.js",
  "max_depth": 2,
  "affected_chunks": 0,
  "affected_files": 0,
  "chunks": "Array(0)"
}
```

**Analysis:** `impact_radius` correctly reports zero affected chunks because the document path points to a non-existent file (it was only registered as a dynamic symbol, never actually created on disk or indexed). This is accurate but provides no actionable signal for dynamic symbol analysis.

---

### coupling — onRecipeCreate.js (2026-04-14T05:30:12Z)

**Params:**
```json
{
  "document_path": "src/services/dynamicSymbolMesh/onRecipeCreate.js",
  "significance_threshold": 0.1
}
```

**Response Summary:**
```json
{
  "document_path": "src/services/dynamicSymbolMesh/onRecipeCreate.js",
  "coupled_files": "Array(0)",
  "total_coupled": 0
}
```

**Analysis:** No coupled files were found. The `coupling` tool relies on static import-graph edges and co-change history, neither of which exist for synthetic dynamic-symbol paths.

---

### llm_embed — onRecipeCreate (2026-04-14T05:30:15Z)

**Params:**
```json
{
  "chunk_id": "stele:dynamic:onRecipeCreate",
  "text": "Dynamic hook fired when a recipe is created..."
}
```

**Response Summary:**
```json
{
  "status": "error",
  "message": "Stele.llm_embed() got an unexpected keyword argument agent_id"
}
```

**Analysis:** The `llm_embed` call failed due to a parameter-passing issue in the MCP bridge. The stele-context server appears to reject calls that carry an implicit `agent_id` field. This is a bug in the tool invocation layer, not in the semantic concept.

---

## Analysis

### What Worked

- **`register_dynamic_symbols`** accepted all 60 symbols in a single batch without errors.
- **`find_references`** successfully resolved dynamically registered symbols, returning proper verdicts and runtime chunk IDs.
- **`impact_radius`** returned structured, well-formed data even for synthetic paths.
- **Symbol graph scaling:** The index handled a +60 symbol injection cleanly (460 docs → 31,133 symbols).

### Gaps Exposed

1. **Dynamic symbols appear unreferenced:** Because `find_references` only scans indexed files, purely dynamic hooks get `verdict: unreferenced`. There is no way to register "synthetic references" that link dynamic symbols to real call sites.
2. **`impact_radius` is empty for non-indexed paths:** While technically correct, it means dynamic symbol impact analysis is impossible without creating real files on disk and re-indexing.
3. **`coupling` requires real import edges:** The tool cannot infer relationships from symbol-name similarity or domain proximity when static edges are absent.
4. **`llm_embed` bridge bug:** The MCP parameter pipeline injects `agent_id` into calls that do not accept it, causing failures.
5. **Agent timeout:** The dedicated stele-context sub-agent timed out after 900 seconds while attempting shell-based MCP invocations, suggesting that the CLI paths for stele-context are slow or unreliable at scale.

### Recommendations

1. Allow `register_dynamic_symbols` to optionally specify `reference` entries in existing indexed files, creating realistic edges for hooks and callbacks.
2. Add a "synthetic edge" mode to `impact_radius` that can traverse dynamically registered symbols even when their documents are not on disk.
3. Enhance `coupling` with a fallback based on shared symbol-name patterns or domain proximity when import edges are absent.
4. Fix the `llm_embed` MCP bridge to filter out unsupported parameters.
5. Provide a batch `store_embedding` API that accepts multiple chunks at once for large dynamic symbol meshes.

---

## Standalone Usage Guide

If you use **only** stele-context, you can still:
- Index your codebase and query symbols with `find_references` and `find_definition`
- Register dynamic symbols for plugin systems, hooks, or runtime callbacks
- Store semantic embeddings for important chunks via `llm_embed`
- Analyze file coupling through the symbol graph
- Detect stale chunks after edits

No coordination layer, no test runner, and no planner are required.

---

## Grading

| Capability | Grade | Notes |
|------------|-------|-------|
| `register_dynamic_symbols` | **A** | Handles 60+ symbols cleanly |
| `find_references` | **B+** | Works, but dynamic symbols appear unreferenced |
| `impact_radius` | **B** | Correct but empty for non-indexed paths |
| `coupling` | **C+** | Requires real files to produce edges |
| `llm_embed` | **D** | Failed due to bridge parameter issue |
| Standalone reliability | **B** | Core symbol tools work without other MCPs |

---

*Report generated by Phase 13 DynamicSymbolMesh*  
*Timestamp: 2026-04-14*

---
## Closure (2026-08-01 audit remediation)

**Status: Closed for product.** llm_embed agent_id, bulk embeddings, impact symbol= seeds shipped. Synthetic reference edges for dynamic-only symbols are **WONTFIX** design ceiling (register + symbol= impact only). See STABILITY.
