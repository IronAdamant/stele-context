#include "util.hpp"

class Accum {
public:
    int add(int n) { total += n; return total; }
private:
    int total = 0;
};

int main() {
    Accum a;
    return a.add(1);
}
