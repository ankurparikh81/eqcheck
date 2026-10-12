int calc(int x) {
    return x + x;
}

// Extra function present in impl that is NEVER called by calc
int uncalled_redundant_helper(int y) {
    return y * y + 10;
}
