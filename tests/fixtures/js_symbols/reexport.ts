// TypeScript / ES re-export alias patterns
export class RecipeSnapshot {
  id: string = "";
}

export { RecipeSnapshot as LineageSnapshot };

export { Foo as FooAlias } from "./other";
