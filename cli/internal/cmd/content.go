package cmd

import (
	"context"
	"errors"
	"strconv"

	"github.com/spf13/cobra"

	"github.com/willem/news_app/cli/internal/api"
	"github.com/willem/news_app/cli/internal/runtime"
)

func (a *App) newContentCommand() *cobra.Command {
	contentCmd := &cobra.Command{
		Use:   "content",
		Short: "List, inspect, and submit content",
	}

	var listArgs struct {
		Limit       int
		Cursor      string
		ContentType []string
		Date        string
		ReadFilter  string
	}
	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List content cards",
		RunE: func(cmd *cobra.Command, _ []string) error {
			return a.runRemote(cmd, "content.list", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				params := api.ListContentParams{}
				params.Limit.SetTo(listArgs.Limit)
				if listArgs.Cursor != "" {
					params.Cursor.SetTo(listArgs.Cursor)
				}
				if len(listArgs.ContentType) > 0 {
					params.ContentType.SetTo(listArgs.ContentType)
				}
				if listArgs.Date != "" {
					params.Date.SetTo(listArgs.Date)
				}
				if listArgs.ReadFilter != "" {
					params.ReadFilter.SetTo(listArgs.ReadFilter)
				}
				data, err := client.ListContent(ctx, params)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}
	listCmd.Flags().IntVar(&listArgs.Limit, "limit", 25, "Max results to return")
	listCmd.Flags().StringVar(&listArgs.Cursor, "cursor", "", "Pagination cursor")
	listCmd.Flags().StringSliceVar(&listArgs.ContentType, "content-type", nil, "Filter by content type")
	listCmd.Flags().StringVar(&listArgs.Date, "date", "", "Filter by local date (YYYY-MM-DD)")
	listCmd.Flags().StringVar(&listArgs.ReadFilter, "read-filter", "all", "Filter by read state")

	getCmd := &cobra.Command{
		Use:   "get <content-id>",
		Short: "Fetch one content item",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			contentID, err := strconv.Atoi(args[0])
			if err != nil {
				return a.renderError("content.get", err)
			}
			return a.runRemote(cmd, "content.get", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.GetContent(ctx, contentID)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}

	contentCmd.AddCommand(
		listCmd,
		getCmd,
		a.newSubmitCommand("submit", "content.submit"),
		a.newSubmitCommand("summarize", "content.summarize"),
	)
	return contentCmd
}

func (a *App) newSubmitCommand(use string, commandName string) *cobra.Command {
	var args struct {
		Note            string
		CrawlLinks      bool
		SubscribeToFeed bool
		Title           string
		Platform        string
		ContentType     string
		Wait            waitFlags
	}

	command := &cobra.Command{
		Use:   use + " <url>",
		Short: "Submit a URL for processing",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, positional []string) error {
			submitURL, err := runtime.ParseURL(positional[0])
			if err != nil {
				return a.renderError(commandName, err)
			}
			if args.Wait.Wait && args.Wait.Interval <= 0 {
				return a.renderError(commandName, errors.New("wait-interval must be greater than zero"))
			}

			request := &api.SubmitContentRequest{}
			request.SetURL(submitURL)
			if args.Note != "" {
				request.Instruction.SetTo(args.Note)
			}
			if args.CrawlLinks {
				request.CrawlLinks.SetTo(true)
			}
			if args.SubscribeToFeed {
				request.SubscribeToFeed.SetTo(true)
			}
			if args.Title != "" {
				request.Title.SetTo(args.Title)
			}
			if args.Platform != "" {
				request.Platform.SetTo(args.Platform)
			}
			if args.ContentType != "" {
				request.ContentType.SetTo(api.ContentType(args.ContentType))
			}

			return a.runRemote(cmd, commandName, func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.SubmitContent(ctx, request)
				if err != nil {
					return commandResult{}, err
				}
				result := commandResult{Data: data}
				if args.Wait.Wait {
					jobID, ok := data.TaskID.Get()
					if !ok {
						return commandResult{}, errors.New("submit response did not include task_id")
					}
					job, err := client.WaitForJob(ctx, jobID, runtime.WaitOptions{
						Interval: args.Wait.Interval,
						Timeout:  args.Wait.Timeout,
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

	command.Flags().StringVar(&args.Note, "note", "", "Instruction or note for the submission")
	command.Flags().BoolVar(&args.CrawlLinks, "crawl-links", false, "Queue discovered links from the page")
	command.Flags().BoolVar(&args.SubscribeToFeed, "subscribe-to-feed", false, "Subscribe to a detected feed instead of processing the page as content")
	command.Flags().StringVar(&args.Title, "title", "", "Optional client-supplied title")
	command.Flags().StringVar(&args.Platform, "platform", "", "Optional platform hint")
	command.Flags().StringVar(&args.ContentType, "content-type", "", "Optional content type hint")
	a.addWaitFlags(command, &args.Wait)
	return command
}
