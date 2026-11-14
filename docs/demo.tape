# Records docs/demo.gif for the README.
#   brew install vhs        (once)
#   make eval ...           (first, so the demo replays from the cache for free)
#   vhs docs/demo.tape
Output docs/demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1300
Set Height 820
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "export COLUMNS=150 && clear" Enter
Show

Type "uv run askoract demo" Sleep 400ms Enter
Sleep 9s
Type "uv run askoract trace latest A-039 --profile full_haiku" Sleep 400ms Enter
Sleep 7s
