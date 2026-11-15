$origin's supervisor needs help. You are on $machine. The watch on $origin found these checks failing, and they are still failing:
$failures

The checks are defined from $origin's point of view ("localhost" in a check means $origin). Reach $origin with:
  $origin_ssh

What each check is, and what usually fixes it:
$checks

About $origin:
$origin_notes

About this machine, $machine:
$notes

What has been tried already. Do not repeat it; start from what was found:
$earlier

Find out why and fix it if you safely can, here or on $origin over ssh. After you finish, the checks are run again from $origin; that result decides whether this is fixed.

Incident $incident. To see the checks as $origin's watch sees them, without changing anything:
  $recheck
