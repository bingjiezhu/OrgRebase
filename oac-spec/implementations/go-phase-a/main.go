package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
)

func main() {
	input, err := io.ReadAll(io.LimitReader(os.Stdin, maxRequestBytes+1))
	if err != nil {
		fmt.Fprintln(os.Stderr, "read request:", err)
		os.Exit(2)
	}
	if len(input) > maxRequestBytes {
		fmt.Fprintln(os.Stderr, "adapter request exceeds maxRequestBytes")
		os.Exit(2)
	}
	writeResponse(handleWireRequest(input))
}

func writeResponse(response wireResponse) {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(response); err != nil {
		fmt.Fprintln(os.Stderr, "encode response:", err)
	}
}
