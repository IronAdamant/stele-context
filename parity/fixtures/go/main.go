package main

import "fmt"

type Box struct {
    N int
}

func (b Box) Value() int {
    return b.N
}

func main() {
    fmt.Println(Box{N: 1}.Value())
}
