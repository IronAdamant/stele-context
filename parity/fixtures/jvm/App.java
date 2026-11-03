package demo;

import java.util.List;

public class Greeter {
    public String hello(String name) {
        return name;
    }

    public int compute(int n) {
        return hello(String.valueOf(n)).length();
    }
}
