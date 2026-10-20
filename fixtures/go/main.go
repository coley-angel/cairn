package main

import (
	"fmt"
	"net/http"
	"os"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "ok\n")
	})
	mux.HandleFunc("/whoami", func(w http.ResponseWriter, r *http.Request) {
		user := r.Header.Get("X-Auth-Request-User")
		if user == "" {
			user = "none"
		}
		fmt.Fprint(w, user)
	})
	if err := http.ListenAndServe(":"+port, mux); err != nil {
		panic(err)
	}
}
