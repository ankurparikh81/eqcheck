int renamed_stage1(int first, int second) {
    return (second * 5) + (first * 3);
}

int pipeline(int x, int y) {
    return renamed_stage1(x, y);
}
