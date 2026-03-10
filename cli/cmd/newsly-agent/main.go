package main

import (
	"context"
	"os"

	"github.com/willem/news_app/cli/internal/cmd"
)

var version = "dev"

func main() {
	app := cmd.New(version, os.Stdout, os.Stderr)
	os.Exit(app.Execute(context.Background(), os.Args[1:]))
}
