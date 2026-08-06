package main

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"os"
	"regexp"
	"time"

	"connectrpc.com/connect"
	"github.com/e2b-dev/infra/packages/shared/pkg/grpc/envd/process"
	"github.com/e2b-dev/infra/packages/shared/pkg/grpc/envd/process/processconnect"
)

const sentinel = "KITDEV_PROXY_COMMAND_OK"
const maxOutputBytes = 4096

func main() {
	if len(os.Args) != 2 || !regexp.MustCompile(`^i[a-z0-9]{20}$`).MatchString(os.Args[1]) {
		panic("invalid sandbox id")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client := processconnect.NewProcessClient(&http.Client{}, "http://127.0.0.1:3002")
	stdin := false
	request := connect.NewRequest(&process.StartRequest{
		Process: &process.ProcessConfig{
			Cmd:  "/bin/sh",
			Args: []string{"-lc", "printf " + sentinel},
		},
		Stdin: &stdin,
	})
	request.Header().Set("E2b-Sandbox-Id", os.Args[1])
	request.Header().Set("E2b-Sandbox-Port", "49983")
	request.Header().Set("Authorization", "Basic cm9vdDo=")
	stream, err := client.Start(ctx, request)
	if err != nil {
		panic(err)
	}
	defer stream.Close()
	var stdout, stderr bytes.Buffer
	receivedBytes := 0
	seenEnd := false
	for stream.Receive() {
		event := stream.Msg().GetEvent()
		if data := event.GetData(); data != nil {
			receivedBytes += len(data.GetStdout()) + len(data.GetStderr())
			if receivedBytes > maxOutputBytes {
				panic("process output exceeded limit")
			}
			stdout.Write(data.GetStdout())
			stderr.Write(data.GetStderr())
		}
		if end := event.GetEnd(); end != nil {
			if seenEnd || !end.GetExited() || end.GetExitCode() != 0 || end.GetError() != "" {
				panic("invalid process end event")
			}
			seenEnd = true
		}
	}
	if err := stream.Err(); err != nil {
		panic(err)
	}
	if !seenEnd || stdout.String() != sentinel || stderr.Len() != 0 {
		panic("process output mismatch")
	}
	fmt.Println("status=pass operation=e2e-proxy-command")
}
