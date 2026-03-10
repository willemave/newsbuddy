package cmd

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"
)

func (a *App) newVersionCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print the CLI version",
		RunE: func(cmd *cobra.Command, _ []string) error {
			return a.runLocal(cmd, "version", func(_ context.Context) (commandResult, error) {
				return commandResult{Data: map[string]string{"version": a.version}}, nil
			})
		},
	}
}

func (a *App) newCompletionCommand(root *cobra.Command) *cobra.Command {
	return &cobra.Command{
		Use:       "completion [bash|zsh|fish|powershell]",
		Short:     "Generate shell completion scripts",
		Args:      cobra.ExactValidArgs(1),
		ValidArgs: []string{"bash", "zsh", "fish", "powershell"},
		RunE: func(_ *cobra.Command, args []string) error {
			switch args[0] {
			case "bash":
				return root.GenBashCompletion(a.stdout)
			case "zsh":
				return root.GenZshCompletion(a.stdout)
			case "fish":
				return root.GenFishCompletion(a.stdout, true)
			case "powershell":
				return root.GenPowerShellCompletionWithDesc(a.stdout)
			default:
				return fmt.Errorf("unsupported shell %q", args[0])
			}
		},
	}
}
