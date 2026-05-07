package cmd

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/spf13/cobra"
)

const libraryManifestFilename = ".newsly-agent-manifest.json"

type localLibraryManifest struct {
	Files map[string]string `json:"files"`
}

func (a *App) newLibraryCommand() *cobra.Command {
	libraryCmd := &cobra.Command{
		Use:   "library",
		Short: "Sync the personal markdown library to local disk",
	}

	args := struct {
		Dir           string
		IncludeSource bool
		AllowPruneAll bool
	}{
		IncludeSource: true,
	}

	syncCmd := &cobra.Command{
		Use:   "sync",
		Short: "Download the current markdown library diff to local disk",
		RunE: func(cmd *cobra.Command, _ []string) error {
			runtimeCfg, err := a.resolveRuntimeConfig()
			if err != nil {
				return a.renderError("library.sync", err)
			}
			if err := runtimeCfg.ValidateRemote(); err != nil {
				return a.renderErrorWithPath("library.sync", runtimeCfg.Path, err)
			}

			client, err := a.newRuntimeClient(runtimeCfg)
			if err != nil {
				return a.renderErrorWithPath("library.sync", runtimeCfg.Path, err)
			}

			libraryRoot := runtimeCfg.LibraryRoot
			if strings.TrimSpace(args.Dir) != "" {
				libraryRoot = args.Dir
			}
			if libraryRoot == "" {
				return a.renderError("library.sync", errors.New("missing library root"))
			}
			libraryRoot = filepath.Clean(libraryRoot)
			if err := os.MkdirAll(libraryRoot, 0o700); err != nil {
				return a.renderError("library.sync", err)
			}

			remoteManifest, err := client.GetLibraryManifest(cmd.Context(), args.IncludeSource)
			if err != nil {
				return a.renderErrorWithPath("library.sync", runtimeCfg.Path, err)
			}
			localManifest, err := loadLocalLibraryManifest(
				filepath.Join(libraryRoot, libraryManifestFilename),
			)
			if err != nil {
				return a.renderError("library.sync", err)
			}
			if len(remoteManifest.Documents) == 0 && len(localManifest.Files) > 0 && !args.AllowPruneAll {
				return a.renderError(
					"library.sync",
					errors.New("remote library manifest is empty; refusing to delete all tracked files without --allow-prune-all"),
				)
			}

			downloaded := 0
			unchanged := 0
			repaired := 0
			remoteFiles := make(map[string]string, len(remoteManifest.Documents))
			for _, document := range remoteManifest.Documents {
				remoteFiles[document.RelativePath] = document.ChecksumSHA256
				targetPath, err := safeLibraryPath(libraryRoot, document.RelativePath)
				if err != nil {
					return a.renderError("library.sync", err)
				}
				if localManifest.Files[document.RelativePath] == document.ChecksumSHA256 {
					if actualChecksum, err := checksumFile(targetPath); err == nil && actualChecksum == document.ChecksumSHA256 {
						unchanged++
						continue
					}
					repaired++
				}

				filePayload, err := client.GetLibraryFile(cmd.Context(), document.RelativePath)
				if err != nil {
					return a.renderErrorWithPath("library.sync", runtimeCfg.Path, err)
				}
				if actualChecksum := checksumText(filePayload.Text); actualChecksum != document.ChecksumSHA256 {
					return a.renderError(
						"library.sync",
						fmt.Errorf("downloaded checksum mismatch for %s", document.RelativePath),
					)
				}
				if err := rejectLibrarySymlinks(libraryRoot, targetPath); err != nil {
					return a.renderError("library.sync", err)
				}
				if err := os.MkdirAll(filepath.Dir(targetPath), 0o700); err != nil {
					return a.renderError("library.sync", err)
				}
				if err := writeFileAtomic(targetPath, []byte(filePayload.Text), 0o600); err != nil {
					return a.renderError("library.sync", err)
				}
				downloaded++
			}

			deleted := 0
			for relativePath := range localManifest.Files {
				if _, ok := remoteFiles[relativePath]; ok {
					continue
				}
				targetPath, err := safeLibraryPath(libraryRoot, relativePath)
				if err != nil {
					return a.renderError("library.sync", err)
				}
				if err := os.Remove(targetPath); err != nil && !os.IsNotExist(err) {
					return a.renderError("library.sync", err)
				}
				if err := pruneEmptyLibraryDirs(filepath.Dir(targetPath), libraryRoot); err != nil {
					return a.renderError("library.sync", err)
				}
				deleted++
			}

			if err := saveLocalLibraryManifest(filepath.Join(libraryRoot, libraryManifestFilename), remoteFiles); err != nil {
				return a.renderError("library.sync", err)
			}

			return a.renderSuccess("library.sync", commandResult{
				Data: map[string]any{
					"library_root":   libraryRoot,
					"downloaded":     downloaded,
					"deleted":        deleted,
					"unchanged":      unchanged,
					"repaired":       repaired,
					"document_count": len(remoteManifest.Documents),
				},
			})
		},
	}

	syncCmd.Flags().StringVar(&args.Dir, "dir", "", "Override the local sync directory")
	syncCmd.Flags().BoolVar(&args.IncludeSource, "include-source", true, "Sync source/full-text markdown alongside summaries")
	syncCmd.Flags().BoolVar(&args.AllowPruneAll, "allow-prune-all", false, "Allow an empty remote manifest to delete all tracked local files")

	libraryCmd.AddCommand(syncCmd)
	return libraryCmd
}

func loadLocalLibraryManifest(path string) (localLibraryManifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return localLibraryManifest{Files: map[string]string{}}, nil
		}
		return localLibraryManifest{}, err
	}
	var manifest localLibraryManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return localLibraryManifest{}, err
	}
	if manifest.Files == nil {
		manifest.Files = map[string]string{}
	}
	return manifest, nil
}

func saveLocalLibraryManifest(path string, files map[string]string) error {
	manifest := localLibraryManifest{Files: files}
	payload, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	payload = append(payload, '\n')
	return writeFileAtomic(path, payload, 0o600)
}

func safeLibraryPath(root string, relativePath string) (string, error) {
	cleanRoot := filepath.Clean(root)
	targetPath := filepath.Clean(filepath.Join(cleanRoot, filepath.FromSlash(relativePath)))
	rel, err := filepath.Rel(cleanRoot, targetPath)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", errors.New("library path escapes the sync root")
	}
	return targetPath, nil
}

func pruneEmptyLibraryDirs(start string, stop string) error {
	current := filepath.Clean(start)
	stop = filepath.Clean(stop)
	for current != stop && current != "." && current != string(filepath.Separator) {
		if err := os.Remove(current); err != nil {
			if os.IsNotExist(err) {
				current = filepath.Dir(current)
				continue
			}
			if errors.Is(err, syscall.ENOTEMPTY) || errors.Is(err, syscall.EEXIST) {
				return nil
			}
			return err
		}
		current = filepath.Dir(current)
	}
	return nil
}

func checksumFile(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return checksumBytes(data), nil
}

func checksumText(text string) string {
	return checksumBytes([]byte(text))
}

func checksumBytes(data []byte) string {
	sum := sha256.Sum256(data)
	return fmt.Sprintf("%x", sum[:])
}

func writeFileAtomic(path string, data []byte, perm os.FileMode) error {
	tmpPath := path + ".tmp"
	if err := os.WriteFile(tmpPath, data, perm); err != nil {
		return err
	}
	if err := os.Chmod(tmpPath, perm); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	if err := os.Rename(tmpPath, path); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	return nil
}

func rejectLibrarySymlinks(root string, targetPath string) error {
	root = filepath.Clean(root)
	targetPath = filepath.Clean(targetPath)
	rel, err := filepath.Rel(root, targetPath)
	if err != nil {
		return err
	}
	current := root
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		if part == "." || part == "" {
			continue
		}
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("refusing to write through symlink in library path: %s", current)
		}
	}
	return nil
}
