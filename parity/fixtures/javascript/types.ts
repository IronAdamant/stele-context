export interface Named {
    name: string;
}

export type Id = string;

export class Store {
    get(id: Id): Named | null {
        return lookup(id);
    }
}

export function lookup(id: Id): Named | null {
    return null;
}
