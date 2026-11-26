package demo

import (
	"crypto/md5"
	"database/sql"
	"fmt"
	"net/http"
	"os/exec"
)

const apiKey = "AKIAIOSFODNN7EXAMPLE"

func FindUser(db *sql.DB, name string) (*sql.Rows, error) {
	return db.Query("SELECT * FROM users WHERE name = '" + name + "'")
}

func Run(cmd string) {
	out, _ := exec.Command("sh", "-c", "ls "+cmd).Output()
	fmt.Println(string(out))
}

func Hash(v string) [16]byte {
	return md5.Sum([]byte(v))
}

func Fetch(urls []string) {
	for _, u := range urls {
		resp, err := http.Get(u)
		if err != nil {
			panic(err)
		}
		defer resp.Body.Close()
	}
}

func Common(a, b []int) []int {
	out := []int{}
	for _, x := range a {
		for _, y := range b {
			for _, z := range a {
				if x == y && y == z {
					out = append(out, x)
				}
			}
		}
	}
	return out
}

func Later() {
	// TODO: implement
}
