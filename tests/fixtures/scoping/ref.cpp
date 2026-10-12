int helper_add(int a, int b) {
    return a + b;
}

int helper_mul(int a, int b) {
    return a * b;
}

int dead_uncalled_fn(int x) {
    return x * 999;
}

int compute_all(int x, int y) {
    int s = helper_add(x, y);
    return helper_mul(s, 10);
}
