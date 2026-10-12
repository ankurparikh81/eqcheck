int helper_add(int a, int b) {
    return b + a;
}

int helper_mul(int a, int b) {
    return b * a;
}

int different_uncalled_fn(double d) {
    return (int)(d * 3.14);
}

int compute_all(int x, int y) {
    int s = helper_add(x, y);
    return helper_mul(s, 10);
}
