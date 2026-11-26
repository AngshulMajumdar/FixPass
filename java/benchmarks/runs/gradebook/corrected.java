import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Scanner;

public class Main {

    static final class Student {
        final String id;
        final String name;
        final int[] scores;

        Student(String id, String name, int[] scores) {
            this.id = id;
            this.name = name;
            this.scores = scores;
        }

        int total() {
            int t = 0;
            for (int s : scores) {
                t += s;
            }
            return t;
        }

        double average() {
            if (scores.length == 0) {
                return 0.0;
            }
            return (double) total() / scores.length;
        }

        int best() {
            int b = Integer.MIN_VALUE;
            for (int s : scores) {
                if (s > b) {
                    b = s;
                }
            }
            return b;
        }
    }

    static int[] weights;
    static Map<String, Student> students = new LinkedHashMap<>();

    static String fmt(double v) {
        long scaled = Math.round(v * 100.0);
        long whole = scaled / 100;
        long frac = Math.abs(scaled % 100);
        return whole + "." + (frac < 10 ? "0" : "") + frac;
    }

    static double weighted(Student s) {
        int wsum = 0;
        double acc = 0.0;
        for (int i = 0; i < s.scores.length; i++) {
            acc += s.scores[i] * weights[i];
            wsum += weights[i];
        }
        if (wsum == 0) {
            return 0.0;
        }
        return acc / wsum;
    }

    static String letter(double score) {
        if (score >= 90.0) {
            return "A";
        } else if (score >= 80.0) {
            return "B";
        } else if (score >= 70.0) {
            return "C";
        } else if (score >= 60.0) {
            return "D";
        }
        return "F";
    }

    static double median(double[] values) {
        if (values.length == 0) {
            return 0.0;
        }
        double[] copy = Arrays.copyOf(values, values.length);
        Arrays.sort(copy);
        int mid = copy.length / 2;
        if (copy.length % 2 > 0) {
            return copy[mid];
        }
        return (copy[mid - 1] + copy[mid]) / 2.0;
    }

    static double stddev(double[] values) {
        if (values.length < 2) {
            return 0.0;
        }
        double mean = 0.0;
        for (double v : values) {
            mean += v;
        }
        mean /= values.length;
        double ss = 0.0;
        for (double v : values) {
            ss += (v - mean) * (v - mean);
        }
        return Math.sqrt(ss / (values.length - 1));
    }

    static void printStudent(Student s) {
        double w = weighted(s);
        System.out.println(s.id + " " + s.name + " total=" + s.total() + " avg=" + fmt(s.average())
                + " weighted=" + fmt(w) + " grade=" + letter(w) + " best=" + s.best());
    }

    static void ranking() {
        List<Student> list = new ArrayList<>(students.values());
        list.sort((a, b) -> {
            int c = Double.compare(weighted(b), weighted(a));
            if (c != 0) {
                return c;
            }
            return a.id.compareTo(b.id);
        });
        int rank = 0;
        double prev = -1.0;
        for (int i = 0; i < list.size(); i++) {
            double w = weighted(list.get(i));
            if (i == 0 || w < prev) {
                rank = i + 1;
            }
            prev = w;
            System.out.println("RANK " + rank + " " + list.get(i).id + " " + fmt(w));
        }
    }

    static void distribution() {
        int[] counts = new int[5];
        String[] labels = {"A", "B", "C", "D", "F"};
        for (Student s : students.values()) {
            String g = letter(weighted(s));
            for (int i = 0; i < labels.length; i++) {
                if (labels[i].equals(g)) {
                    counts[i]++;
                }
            }
        }
        StringBuilder sb = new StringBuilder("DIST");
        for (int i = 0; i < labels.length; i++) {
            sb.append(' ').append(labels[i]).append('=').append(counts[i]);
        }
        System.out.println(sb);
    }

    static void assessmentStats(int idx) {
        if (idx < 0 || idx >= weights.length) {
            System.out.println("ERROR bad assessment " + idx);
            return;
        }
        double[] col = new double[students.size()];
        int k = 0;
        int lowest = Integer.MAX_VALUE;
        for (Student s : students.values()) {
            col[k++] = s.scores[idx];
            lowest = Math.min(lowest, s.scores[idx]);
        }
        if (k == 0) {
            System.out.println("STATS " + idx + " empty");
            return;
        }
        System.out.println("STATS " + idx + " median=" + fmt(median(col)) + " sd=" + fmt(stddev(col))
                + " min=" + lowest);
    }

    static void curve(int points) {
        for (Student s : students.values()) {
            for (int i = 0; i < s.scores.length; i++) {
                s.scores[i] = Math.min(100, s.scores[i] + points);
            }
        }
        System.out.println("CURVED " + points);
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int m = sc.nextInt();
        weights = new int[m];
        for (int i = 0; i < m; i++) {
            weights[i] = sc.nextInt();
        }
        int n = sc.nextInt();
        for (int i = 0; i < n; i++) {
            String id = sc.next();
            String name = sc.next();
            int[] scores = new int[m];
            for (int j = 0; j < m; j++) {
                scores[j] = sc.nextInt();
            }
            students.put(id, new Student(id, name, scores));
        }
        while (sc.hasNext()) {
            String cmd = sc.next();
            if (cmd.equals("SHOW")) {
                String id = sc.next();
                Student s = students.get(id);
                if (s == null) {
                    System.out.println("ERROR no student " + id);
                } else {
                    printStudent(s);
                }
            } else if (cmd.equals("ALL")) {
                for (Student s : students.values()) {
                    printStudent(s);
                }
            } else if (cmd.equals("RANK")) {
                ranking();
            } else if (cmd.equals("DIST")) {
                distribution();
            } else if (cmd.equals("STATS")) {
                assessmentStats(sc.nextInt());
            } else if (cmd.equals("CURVE")) {
                curve(sc.nextInt());
            } else {
                System.out.println("UNKNOWN " + cmd);
            }
        }
    }
}
