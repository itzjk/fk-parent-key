Output docs/demo.gif
Set Shell "bash"
Set FontSize 17
Set Width 1420
Set Height 650
Set Padding 18
Set TypingSpeed 25ms
Set Theme "Dracula"
Hide
Type "unset HISTFILE; export PS1='$ '; export DEMO_DIR=$(mktemp -d); python3 examples/four_cases.py $DEMO_DIR"
Enter
Sleep 1s
Type "clear"
Enter
Show
Type "fk-parent-key check $DEMO_DIR/B.sqlite"
Enter
Sleep 8s
Hide
Type "clear"
Enter
Show
Type "fk-parent-key check $DEMO_DIR/C.sqlite"
Enter
Sleep 8s
