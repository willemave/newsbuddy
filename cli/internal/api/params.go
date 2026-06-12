package api

type ListContentSubmissionStatusesParams struct {
	Cursor *string
	Limit  *int
}

type ListContentsParams struct {
	ContentType           []string
	Date                  *string
	ReadFilter            *string
	Cursor                *string
	Limit                 *int
	IncludeAvailableDates *bool
}

type ListNewsItemsParams struct {
	ReadFilter *string
	Cursor     *string
	Limit      *int
}

type ListScraperConfigsParams struct {
	Type         *string
	Types        *string
	IncludeStats *bool
}

func Ptr[T any](value T) *T {
	return &value
}
