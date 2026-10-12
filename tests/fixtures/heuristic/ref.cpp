int stage1(int a, int b) {
    return (a * 3) + (b * 5);
}

int pipeline(int x, int y) {
    return stage1(x, y);
}
