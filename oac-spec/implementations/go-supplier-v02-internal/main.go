package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
)

func main() {
	raw, err := io.ReadAll(io.LimitReader(os.Stdin, maxRequestBytes+1))
	if err != nil {
		fmt.Fprintln(os.Stderr, "read request:", err)
		os.Exit(2)
	}
	if len(raw) > maxRequestBytes {
		fmt.Fprintln(os.Stderr, "request exceeds maxRequestBytes")
		os.Exit(2)
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(handleWireRequest(raw)); err != nil {
		fmt.Fprintln(os.Stderr, "encode response:", err)
		os.Exit(2)
	}
}
