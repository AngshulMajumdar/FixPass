import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public class Main {

    static List<String> words(String line) {
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        for (int i = 0; i < line.length(); i++) {
            char ch = line.charAt(i);
            if (Character.isLetterOrDigit(ch)) {
                cur.append(Character.toLowerCase(ch));
            } else if (cur.length() > 0) {
                out.add(cur.toString());
                cur.setLength(0);
            }
        }
        if (cur.length() > 0) {
            out.add(cur.toString());
        }
        return out;
    }

    static boolean isPalindrome(String s) {
        int i = 0;
        int j = s.length() - 1;
        while (i < j) {
            if (s.charAt(i) != s.charAt(j)) {
                return false;
            }
            i++;
            j--;
        }
        return true;
    }

    static String caesar(String s, int shift) {
        int k = ((shift % 26) + 26) % 26;
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            if (ch >= 'a' && ch <= 'z') {
                sb.append((char) ('a' + (ch - 'a' + k) % 26));
            } else if (ch >= 'A' && ch <= 'Z') {
                sb.append((char) ('A' + (ch - 'A' + k) % 26));
            } else {
                sb.append(ch);
            }
        }
        return sb.toString();
    }

    static int vowels(String s) {
        int count = 0;
        String v = "aeiouAEIOU";
        for (int i = 0; i < s.length(); i++) {
            if (v.indexOf(s.charAt(i)) >= 0) {
                count++;
            }
        }
        return count;
    }

    static String compress(String s) {
        if (s.isEmpty()) {
            return "";
        }
        StringBuilder sb = new StringBuilder();
        char prev = s.charAt(0);
        int run = 1;
        for (int i = 1; i < s.length(); i++) {
            char ch = s.charAt(i);
            if (ch == prev) {
                run++;
            } else {
                sb.append(prev).append(run);
                prev = ch;
                run = 1;
            }
        }
        sb.append(prev).append(run);
        return sb.toString();
    }

    static String longestWord(List<String> ws) {
        String best = "";
        for (String w : ws) {
            if (w.length() > best.length() || (w.length() == best.length() && w.compareTo(best) < 0)) {
                best = w;
            }
        }
        return best;
    }

    static String reverseWords(String line) {
        String[] parts = line.trim().split("\\s+");
        StringBuilder sb = new StringBuilder();
        for (int i = parts.length - 1; i >= 0; i--) {
            sb.append(parts[i]);
            if (i > 0) {
                sb.append(' ');
            }
        }
        return sb.toString();
    }

    static boolean anagrams(String a, String b) {
        int[] counts = new int[26];
        for (char ch : a.toLowerCase().toCharArray()) {
            if (ch >= 'a' && ch <= 'z') {
                counts[ch - 'a']++;
            }
        }
        for (char ch : b.toLowerCase().toCharArray()) {
            if (ch >= 'a' && ch <= 'z') {
                counts[ch - 'a']--;
            }
        }
        for (int c : counts) {
            if (c != 0) {
                return false;
            }
        }
        return true;
    }

    static String freq(List<String> ws, int top) {
        Map<String, Integer> counts = new TreeMap<>();
        for (String w : ws) {
            counts.merge(w, 1, Integer::sum);
        }
        List<Map.Entry<String, Integer>> entries = new ArrayList<>(counts.entrySet());
        entries.sort((x, y) -> {
            int c = y.getValue() - x.getValue();
            return c != 0 ? c : x.getKey().compareTo(y.getKey());
        });
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < Math.min(top, entries.size()); i++) {
            if (i > 0) {
                sb.append(' ');
            }
            sb.append(entries.get(i).getKey()).append(':').append(entries.get(i).getValue());
        }
        return sb.toString();
    }

    public static void main(String[] args) throws IOException {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        List<String> all = new ArrayList<>();
        String line;
        int lineNo = 0;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) {
                continue;
            }
            lineNo++;
            int sp = line.indexOf(' ');
            String cmd = sp < 0 ? line : line.substring(0, sp);
            String arg = sp < 0 ? "" : line.substring(sp + 1);
            if (cmd.equals("TEXT")) {
                List<String> ws = words(arg);
                all.addAll(ws);
                System.out.println(lineNo + " WORDS " + ws.size() + " VOWELS " + vowels(arg));
            } else if (cmd.equals("PAL")) {
                int count = 0;
                for (String w : words(arg)) {
                    if (w.length() > 1 && isPalindrome(w)) {
                        count++;
                    }
                }
                System.out.println(lineNo + " PAL " + count);
            } else if (cmd.equals("CAESAR")) {
                int sp2 = arg.indexOf(' ');
                int shift = Integer.parseInt(arg.substring(0, sp2));
                System.out.println(lineNo + " CAESAR " + caesar(arg.substring(sp2 + 1), shift));
            } else if (cmd.equals("RLE")) {
                System.out.println(lineNo + " RLE " + compress(arg));
            } else if (cmd.equals("REV")) {
                System.out.println(lineNo + " REV " + reverseWords(arg));
            } else if (cmd.equals("ANA")) {
                String[] p = arg.split("\\s+");
                System.out.println(lineNo + " ANA " + (anagrams(p[0], p[1]) ? "yes" : "no"));
            } else if (cmd.equals("LONGEST")) {
                System.out.println(lineNo + " LONGEST " + longestWord(all));
            } else if (cmd.equals("FREQ")) {
                System.out.println(lineNo + " FREQ " + freq(all, Integer.parseInt(arg.trim())));
            } else {
                System.out.println(lineNo + " UNKNOWN " + cmd);
            }
        }
    }
}
