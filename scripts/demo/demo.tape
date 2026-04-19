# VHS script — demo GIF for PH/HN
# Run: vhs scripts/demo/demo.tape
# Writes:  src/agentgate/server/static/demo.gif
#
# The actual demo content lives in demo-deploy.sh, so VHS only has to
# launch one command and capture the output. This keeps the .tape
# trivial and the timing under our control.

Output src/agentgate/server/static/demo.gif

Set Shell "bash"
Env BASH_ENV ""
Env ENV ""
Set FontSize 15
Set Width 920
Set Height 520
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms
Set LineHeight 1.25
Set Padding 22

Hide
Type `PS1='$ '`
Enter
Type `clear`
Enter
Show
Sleep 500ms

Type `./scripts/demo/demo-deploy.sh`
Enter
Sleep 14s
