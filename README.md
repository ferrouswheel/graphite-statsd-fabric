graphite-statsd-fabric
======================

Yet another guide to configure a working metrics solution.

There are many guides out there, but they all have their own variations which
were not quite satisfactory for my use-case. What is my use-case you ask? It
is:

* We use LXCs for our app isolation. So I don't need to add virtualenvs into the mix.
* I want to use the graphite-project/ceres backend for storage instead of whisper.
* Use `nginx` and `uwsgi` to host graphite-project/graphite-web.
* Provide Ubuntu upstart scripts.
* No idea how prone graphite things are to crashing, but a carbon-daemon
  randomly disappeared on me, so I added a couple of monit scripts people had made.
* While graphite is okay for navigating your metrics, I like the direction of
  team-dashboard http://fdietz.github.io/team_dashboard/ so have added a task for that too.

## To use:

1. Change TIMEZONE and SERVER_NAME at the top of the fabric script.
2. `fab -H YOUR_SERVER monitor_all_the_things`
3. If you want `team_dashboard`, then run `fab -H YOUR_SERVER team_dashboard:PG_SERVER_IP`.
   This assumes you have a user team_dashboard with createdb access to that postgresql db.

## Why don't you use puppet/chef/other?!

Fabric is what I know at the moment, but I'll certainly consider these options
once I've had a chance to learn them.

