import { readFile } from "fs";
const path = require("path");

export function greet(name) {
    return format(name);
}

export class Greeter {
    hello(name) {
        return greet(name);
    }
}

const answer = (n) => n + 1;
module.exports = { Greeter, answer };
