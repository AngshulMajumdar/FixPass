import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Scanner;
import java.util.TreeMap;

public class Main {

    static final class Item {
        String name;
        int qty;
        int price;
        int transactions;
        int netChange;

        Item(String name, int qty, int price) {
            this.name = name;
            this.qty = qty;
            this.price = price;
            this.transactions = 1;
            this.netChange = qty;
        }

        long value() {
            return (long) qty * price;
        }
    }

    static Map<String, Item> items = new TreeMap<>();
    static Map<String, Integer> errorCounts = new HashMap<>();

    static void error(String kind, String message) {
        errorCounts.merge(kind, 1, Integer::sum);
        System.out.println("ERROR " + message);
    }

    static String money(long cents) {
        long whole = cents / 100;
        long frac = cents % 100;
        String f = frac < 10 ? "0" + frac : String.valueOf(frac);
        return whole + "." + f;
    }

    static void add(String name, int qty, int price) {
        if (qty <= 0 || price < 0) {
            error("invalid", "invalid quantity or price for " + name);
            return;
        }
        Item existing = items.get(name);
        if (existing != null) {
            error("duplicate", "item exists " + name);
            return;
        }
        items.put(name, new Item(name, qty, price));
        System.out.println("ADDED " + name);
    }

    static void sell(String name, int qty) {
        Item it = items.get(name);
        if (it == null) {
            error("missing", "no item " + name);
            return;
        }
        if (qty > it.qty) {
            error("stock", "insufficient stock for " + name);
            return;
        }
        it.qty -= qty;
        it.transactions++;
        it.netChange -= qty;
        System.out.println("SOLD " + qty + " " + name + " " + money((long) qty * it.price));
    }

    static void restock(String name, int qty) {
        Item it = items.get(name);
        if (it == null) {
            error("missing", "no item " + name);
            return;
        }
        it.qty += qty;
        it.transactions++;
        it.netChange += qty;
        System.out.println("RESTOCKED " + name + " " + it.qty);
    }

    static void setPrice(String name, int price) {
        Item it = items.get(name);
        if (it == null) {
            error("missing", "no item " + name);
            return;
        }
        int old = it.price;
        it.price = price;
        int diff = price - old;
        String sign = diff >= 0 ? "+" : "-";
        System.out.println("PRICE " + name + " " + sign + money(Math.abs(diff)));
    }

    static void report() {
        System.out.println("REPORT " + items.size());
        for (Item it : items.values()) {
            System.out.println(it.name + " " + it.qty + " " + money(it.price) + " " + money(it.value()));
        }
    }

    static long totalValue() {
        long total = 0;
        for (Item it : items.values()) {
            total += it.value();
        }
        return total;
    }

    static void top(int k) {
        List<Item> list = new ArrayList<>(items.values());
        list.sort(new Comparator<Item>() {
            public int compare(Item a, Item b) {
                int c = Long.compare(b.value(), a.value());
                if (c != 0) {
                    return c;
                }
                return a.name.compareTo(b.name);
            }
        });
        int limit = Math.min(k, list.size());
        System.out.println("TOP " + limit);
        for (int i = 0; i < limit; i++) {
            Item it = list.get(i);
            System.out.println((i + 1) + ". " + it.name + " " + money(it.value()));
        }
    }

    static void low(int threshold) {
        List<String> names = new ArrayList<>();
        for (Item it : items.values()) {
            if (it.qty < threshold) {
                names.add(it.name);
            }
        }
        Collections.sort(names);
        System.out.println("LOW " + names.size() + (names.isEmpty() ? "" : " " + String.join(",", names)));
    }

    static void history(String name) {
        Item it = items.get(name);
        if (it == null) {
            error("missing", "no item " + name);
            return;
        }
        System.out.println("HISTORY " + name + " tx=" + it.transactions + " net=" + it.netChange);
    }

    static void errors() {
        int total = 0;
        StringBuilder sb = new StringBuilder();
        for (String kind : new String[] {"invalid", "duplicate", "missing", "stock"}) {
            int c = errorCounts.getOrDefault(kind, 0);
            total += c;
            sb.append(" ").append(kind).append("=").append(c);
        }
        System.out.println("ERRORS " + total + sb);
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        while (sc.hasNext()) {
            String cmd = sc.next();
            if (cmd.equals("END")) {
                break;
            }
            switch (cmd) {
                case "ADD":
                    add(sc.next(), sc.nextInt(), sc.nextInt());
                    break;
                case "SELL":
                    sell(sc.next(), sc.nextInt());
                    break;
                case "RESTOCK":
                    restock(sc.next(), sc.nextInt());
                    break;
                case "PRICE":
                    setPrice(sc.next(), sc.nextInt());
                    break;
                case "REPORT":
                    report();
                    break;
                case "TOTAL":
                    System.out.println("TOTAL " + money(totalValue()));
                    break;
                case "TOP":
                    top(sc.nextInt());
                    break;
                case "LOW":
                    low(sc.nextInt());
                    break;
                case "HISTORY":
                    history(sc.next());
                    break;
                case "ERRORS":
                    errors();
                    break;
                default:
                    System.out.println("UNKNOWN " + cmd);
            }
        }
    }
}
