int calc_discount(int price, int percent) {
    // Bug: divides by 10 instead of 100
    return price - (price * percent) / 10;
}
