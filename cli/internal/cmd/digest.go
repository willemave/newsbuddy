package cmd

import (
	"context"
	"errors"
	"time"

	"github.com/spf13/cobra"

	"github.com/willem/news_app/cli/internal/api"
	"github.com/willem/news_app/cli/internal/runtime"
)

func (a *App) newDigestCommand() *cobra.Command {
	digestCmd := &cobra.Command{
		Use:     "digest",
		Aliases: []string{"digests"},
		Short:   "Generate and list daily digests",
	}

	var generateArgs struct {
		StartAt string
		EndAt   string
		Form    string
		Wait    waitFlags
	}
	generateCmd := &cobra.Command{
		Use:   "generate",
		Short: "Generate a digest for an arbitrary time window",
		RunE: func(cmd *cobra.Command, _ []string) error {
			if generateArgs.Wait.Wait && generateArgs.Wait.Interval <= 0 {
				return a.renderError("digest.generate", errors.New("wait-interval must be greater than zero"))
			}
			startAt, err := time.Parse(time.RFC3339, generateArgs.StartAt)
			if err != nil {
				return a.renderError("digest.generate", err)
			}
			endAt, err := time.Parse(time.RFC3339, generateArgs.EndAt)
			if err != nil {
				return a.renderError("digest.generate", err)
			}
			request := &api.AgentDigestRequest{
				StartAt: startAt,
				EndAt:   endAt,
			}
			request.Form.SetTo(api.AgentDigestRequestForm(generateArgs.Form))

			return a.runRemote(cmd, "digest.generate", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.GenerateDigest(ctx, request)
				if err != nil {
					return commandResult{}, err
				}
				result := commandResult{Data: data}
				if generateArgs.Wait.Wait {
					job, err := client.WaitForJob(ctx, data.JobID, runtime.WaitOptions{
						Interval: generateArgs.Wait.Interval,
						Timeout:  generateArgs.Wait.Timeout,
					})
					if err != nil {
						return commandResult{}, err
					}
					result.Job = job
				}
				return result, nil
			})
		},
	}
	generateCmd.Flags().StringVar(&generateArgs.StartAt, "start-at", "", "Inclusive RFC3339 start time")
	generateCmd.Flags().StringVar(&generateArgs.EndAt, "end-at", "", "Exclusive RFC3339 end time")
	generateCmd.Flags().StringVar(&generateArgs.Form, "form", "short", "Digest form: short or long")
	_ = generateCmd.MarkFlagRequired("start-at")
	_ = generateCmd.MarkFlagRequired("end-at")
	a.addWaitFlags(generateCmd, &generateArgs.Wait)

	var listArgs struct {
		Limit      int
		Cursor     string
		ReadFilter string
	}
	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List generated daily digests",
		RunE: func(cmd *cobra.Command, _ []string) error {
			return a.runRemote(cmd, "digest.list", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				params := api.ListDigestsParams{}
				params.Limit.SetTo(listArgs.Limit)
				params.ReadFilter.SetTo(listArgs.ReadFilter)
				if listArgs.Cursor != "" {
					params.Cursor.SetTo(listArgs.Cursor)
				}
				data, err := client.ListDigests(ctx, params)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}
	listCmd.Flags().IntVar(&listArgs.Limit, "limit", 25, "Max digests to return")
	listCmd.Flags().StringVar(&listArgs.Cursor, "cursor", "", "Pagination cursor")
	listCmd.Flags().StringVar(&listArgs.ReadFilter, "read-filter", "unread", "Read filter: unread, read, or all")

	digestCmd.AddCommand(generateCmd, listCmd)
	return digestCmd
}
