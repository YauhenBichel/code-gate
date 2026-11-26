package demo;

import java.sql.*;
import java.util.*;
import java.security.MessageDigest;

public class Bad {
    private static final String PASSWORD = "S3cretPassw0rd!2026";

    public ResultSet findUser(Connection c, String name) throws Exception {
        Statement s = c.createStatement();
        return s.executeQuery("SELECT * FROM users WHERE name = '" + name + "'");
    }

    public void run(String cmd) throws Exception {
        Runtime.getRuntime().exec("ls " + cmd);
    }

    public byte[] hash(String v) throws Exception {
        return MessageDigest.getInstance("MD5").digest(v.getBytes());
    }

    public List<Integer> common(List<Integer> a, List<Integer> b) {
        List<Integer> out = new ArrayList<>();
        for (Integer x : a) {
            if (b.contains(x)) {
                out.add(x);
            }
        }
        return out;
    }

    public String join(List<String> parts) {
        String s = "";
        for (String p : parts) {
            s += p;
        }
        return s;
    }

    public void save(String data) {
        try {
            System.out.println(data);
        } catch (Exception e) {
        }
        try {
            data.length();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    public boolean same(String a, String b) {
        return a == "yes";
    }
}
