import Foundation

public struct Greeter {
    public func hello(name: String) -> String {
        return name
    }
}

public class Runner {
    public func run() -> Int {
        return Compute(1)
    }
}

func Compute(_ n: Int) -> Int {
    return n + 1
}
