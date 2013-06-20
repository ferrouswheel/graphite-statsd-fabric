from StringIO import StringIO

from fabric.api import *
from fabric.context_managers import shell_env
from fabric.contrib.files import append


# Best to leave GRAPHITE_ROOT as is, since this appears to be standard from graphite
# installes and I have not tested other destinations.
GRAPHITE_ROOT='/opt/graphite'

# This is for the graphite django webapp. Not providing this correctly will result
# it your metrics being display at the wrong time. Presumedly metrics are
# stored in unix time everywhere else...
#
# TODO: update team_dashboard's config/application.rb to use this timezone too.
# Currently you need to manually add:
#   config.time_zone = 'Auckland'
#   config.active_record.default_timezone = 'Auckland'
TIMEZONE='Pacific/Auckland'


def run_updates():
    sudo('apt-get update')
    sudo('apt-get install -y aptitude')
    packages = [
            'aptitude', 'upstart', 'monit', 'git',
            'python-pip', 'python-dev',
            'libcairo2', 'libffi-dev', 'memcached', 'nginx', 
            'uwsgi', 'uwsgi-plugin-python', 'uwsgi-plugin-carbon'
            ]

    sudo('aptitude install -y ' + ' '.join(packages))
    sudo('pip install -U pip') # Get the latest pip

    # Not needed, because we assume we are in a LXC
    #sudo('pip install -U virtualenvwrapper') # Get the latest virtualenvwrapper 


def add_graphite_user_and_dir():
    sudo('adduser --gecos "" --disabled-password --quiet graphite')

    sudo('mkdir -p /opt/graphite')
    sudo('mkdir -p /opt/graphite/src')
    sudo('chown -R graphite:graphite /opt/graphite')


def install_ceres():
    with cd(GRAPHITE_ROOT + '/src'):
        sudo('git clone git://github.com/graphite-project/ceres.git')
        with cd('ceres/'):
            sudo('pip install -r requirements.txt')
            sudo('python setup.py install')

        sudo('ceres-tree-create /opt/graphite/storage/ceres')


def install_carbon():
    with cd(GRAPHITE_ROOT + '/src'):
        sudo('git clone git://github.com/graphite-project/carbon.git -b megacarbon')
        with cd('carbon/'):
            sudo('pip install -r requirements.txt')
            sudo('python setup.py install')

        with cd(GRAPHITE_ROOT + '/conf/carbon-daemons/'):
            sudo('cp -r example/ writer')
            with cd('writer/'):
                sudo("sed -i 's/^USER = .*$/USER = graphite/' daemon.conf")

                sudo("sed -i 's/^DATABASE = .*$/DATABASE = ceres/' db.conf")
                sudo(r"sed -i 's/^LOCAL_DATA_DIR = .*$/LOCAL_DATA_DIR = \/opt\/graphite\/storage\/ceres\//' db.conf")

    carbon_conf = """description "carbon writer"

start on startup
stop on shutdown

expect daemon

respawn limit 5 10

env GRAPHITE_DIR=/opt/graphite
exec start-stop-daemon --oknodo --pidfile /var/run/carbon-writer.pid --startas $GRAPHITE_DIR/bin/carbon-daemon.py --start writer start
"""
    put(StringIO(carbon_conf), '/etc/init/carbon-writer.conf', use_sudo=True)
    fix_permissions()
    sudo('service carbon-writer start')

    carbon_monit = """#!monit
set logfile /var/log/monit.log

check process python with pidfile "/var/run/carbon-writer.pid"
    start program = "/sbin/start carbon-writer"
    stop program  = "/sbin/stop carbon-writer"
"""
    put(StringIO(carbon_monit), '/etc/monit/conf.d/carbon-writer.conf', use_sudo=True)

    sudo('/etc/init.d/monit restart')


def install_webapp():
    with cd(GRAPHITE_ROOT + '/src'):
        put(StringIO('from cairocffi import *'), '/usr/local/lib/python2.7/dist-packages/cairo.py', use_sudo=True)

        sudo('git clone git://github.com/graphite-project/graphite-web.git')
        with cd('graphite-web/'):
            sudo("sed -i '/cairo/d' requirements.txt")
            sudo('pip install -r requirements.txt')
            sudo('pip install cairocffi')
            sudo('python setup.py install')

    with cd(GRAPHITE_ROOT + '/webapp/'):
        sudo("cp /opt/graphite/conf/graphite.wsgi.example wsgi.py")

        with cd('graphite'):
            sudo("cp local_settings.py.example local_settings.py")
            if TIMEZONE:
                sudo("sed -i -e 's/^#TIMEZONE = .*$/TIMEZONE = \"%s\"/' local_settings.py" % TIMEZONE.replace('/', '\/'))
            sudo('python manage.py syncdb --noinput')


def setup_nginx_and_uwsgi():
    nginx_conf = """server {
  listen 80;
  keepalive_timeout 60;
  server_name _;
  charset utf-8;
  location / {
    include uwsgi_params;
    uwsgi_param UWSGI_CHDIR /opt/graphite/webapp/;
    uwsgi_param UWSGI_MODULE wsgi;
    uwsgi_param UWSGI_CALLABLE app;
    uwsgi_pass 127.0.0.1:3031;
  }
  location /content/ {
    alias /opt/graphite/webapp/content/;
    autoindex off;
  }
}"""
    put(StringIO(nginx_conf), '/etc/nginx/sites-available/graphite', use_sudo=True)
    sudo('rm /etc/nginx/sites-available/default')

    uwsgi_config = """[uwsgi]
plugins = python
gid = graphite
uid = graphite
vhost = true
logdate
socket = 127.0.0.1:3031
master = true
processes = 4
harakiri = 20
limit-as = 256
wsgi-file = /opt/graphite/webapp/wsgi.py
chdir = /opt/graphite
memory-report
no-orphans
carbon = 127.0.0.1:2003
"""
    put(StringIO(uwsgi_config), '/etc/uwsgi/apps-available/graphite.ini', use_sudo=True)

    #By default in Ubuntu, uwsgi will run as the www-data user and cannot
    #change privileges to the graphite user. To work around this, we can modify
    #the default configuration at /usr/share/uwsgi/conf/default.ini and remove
    #the uid and gid lines.
    sudo("sed -i -e '/^uid =/s/^/#/'  /usr/share/uwsgi/conf/default.ini")
    sudo("sed -i -e '/^gid =/s/^/#/' /usr/share/uwsgi/conf/default.ini")

    sudo('ln -s /etc/nginx/sites-{available,enabled}/graphite')
    sudo('ln -s /etc/uwsgi/apps-{available,enabled}/graphite.ini')

    
def fix_permissions():
    sudo("chown -R graphite.graphite /opt/graphite/")
    sudo("chmod u+rwX -R /opt/graphite/")
    sudo("chmod g+rwX -R /opt/graphite/")
    sudo("chmod o-rw -R /opt/graphite/")


def web_permissions():
    sudo("chown -R graphite.www-data /opt/graphite/webapp/content")
    sudo("chmod o-rw -R /opt/graphite/webapp/content")


@task
def setup_graphite():
    run_updates()
    add_graphite_user_and_dir()

    install_ceres()
    install_carbon()
    install_webapp()
    setup_nginx_and_uwsgi()
    fix_permissions()
    web_permissions()

    # start services
    sudo('service nginx restart')
    sudo('service uwsgi restart')


@task
def setup_node():
    sudo('apt-get install -y python-software-properties')
    sudo('apt-add-repository -y ppa:chris-lea/node.js')
    sudo('apt-get update')
    sudo('apt-get install -y nodejs')

    with cd('/opt'):
        sudo('git clone git://github.com/etsy/statsd.git')
        statsd_config = """
{
  graphitePort: 2003
, graphiteHost: "127.0.0.1"
, port: 8125
}
"""
        put(StringIO(statsd_config), '/opt/statsd/localConfig.js', use_sudo=True)


@task
def setup_statsd():
    setup_node()
    # Upstart job and monit by http://zzarbi.tumblr.com/post/43762180430/statsd-and-ubuntu-server-12-10
    statsd_init = """#!upstart
description "Statsd node.js server"
author      "Nicolas"

start on startup
stop on shutdown

script
    export HOME="/root"

    echo $$ > /var/run/statsd.pid
    exec sudo -u www-data /usr/bin/nodejs /opt/statsd/stats.js /opt/statsd/localConfig.js  >> /var/log/statsd.log 2> /var/log/statsd.error.log
end script

pre-start script
    # Date format same as (new Date()).toISOString() for consistency
    echo "[`date -u +%Y-%m-%dT%T.%3NZ`] (sys) Starting" >> /var/log/statsd.log
end script

pre-stop script
    rm /var/run/statsd.pid
    echo "[`date -u +%Y-%m-%dT%T.%3NZ`] (sys) Stopping" >> /var/log/statsd.log
end script
"""
    put(StringIO(statsd_init), '/etc/init/statsd.conf', use_sudo=True)
    sudo('service statsd start')

    statsd_monit = """#!monit
set logfile /var/log/monit.log

check process nodejs with pidfile "/var/run/statsd.pid"
    start program = "/sbin/start statsd"
    stop program  = "/sbin/stop statsd"
"""
    put(StringIO(statsd_monit), '/etc/monit/conf.d/statsd.conf', use_sudo=True)

    sudo('/etc/init.d/monit restart')


@task
def get_ruby():
    # Following from
    # http://leonard.io/blog/2012/05/installing-ruby-1-9-3-on-ubuntu-12-04-precise-pengolin/
    sudo('apt-get update')

    sudo(r"""apt-get install -y ruby1.9.1 ruby1.9.1-dev \
      rubygems1.9.1 irb1.9.1 ri1.9.1 rdoc1.9.1 \
      build-essential libopenssl-ruby1.9.1 libssl-dev zlib1g-dev""")


    sudo(r"""update-alternatives --install /usr/bin/ruby ruby /usr/bin/ruby1.9.1 400 \
             --slave   /usr/share/man/man1/ruby.1.gz ruby.1.gz \
                            /usr/share/man/man1/ruby1.9.1.1.gz \
            --slave   /usr/bin/ri ri /usr/bin/ri1.9.1 \
            --slave   /usr/bin/irb irb /usr/bin/irb1.9.1 \
            --slave   /usr/bin/rdoc rdoc /usr/bin/rdoc1.9.1""")

    # choose your interpreter
    # changes symlinks for /usr/bin/ruby , /usr/bin/gem
    # /usr/bin/irb, /usr/bin/ri and man (1) ruby
    sudo('update-alternatives --config ruby')
    sudo('update-alternatives --config gem')


@task
def setup_team_dashboard(PG_DB):
    """
    Because the intent is to install into an LXC, and we are using this
    for internal network use, we assume that PG has pg_hba.conf setup
    to allow trusted connections (i.e. no authentication required).

    We also assume a teamdashboard user exists with createdb permissions.
    """
    sudo('apt-get install -y postgresql-client libpq-dev libxml2-dev libmysqlclient-dev libxslt-dev')
    get_ruby()
    with cd('/opt/'):
        sudo('git clone https://github.com/fdietz/team_dashboard.git')
        
        sudo('gem install bundler')
        with cd('team_dashboard'):
            append('Gemfile', 'gem "pg"', use_sudo=True)
            sudo('bundle install')
            db_conf="""common: &common
  adapter: postgresql
  host: %s
  username: teamdashboard 

development:
  <<: *common
  database: team_dashboard_development

test:
  <<: *common
  database: team_dashboard_test

production:
  <<: *common
  database: team_dashboard_production
    """ % PG_DB
            put(StringIO(db_conf), 'config/database.yml', use_sudo=True)

            sudo("sed -i 's/^listen /^#listen /' config/unicorn.rb")
            append('config/unicorn.rb', """
listen "/tmp/.unicorn.sock.0", :backlog => 64
listen "/tmp/.unicorn.sock.1", :backlog => 64
""", use_sudo=True)
            with shell_env(RAILS_ENV=production):
                sudo('rake db:create')
                sudo('rake db:migrate')
                sudo('rake assets:precompile')

    td_nginx = """
upstream backend {
    server unix:/tmp/.unicorn.sock.0;
    server unix:/tmp/.unicorn.sock.1;
}
 
server {
    listen 8081;
    server_name _; # all accept
    access_log /var/log/nginx/access.log;
     
    location ~ ^/assets/ {
        root /opt/team_dashboard/public;
        gzip_static on; # to serve pre-gzipped version
        expires 1y;
        add_header Cache-Control public;
        add_header ETag "";
        break;
    }
     
    location / {
        proxy_set_header HOST $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_pass http://backend;
        proxy_redirect off;
    }
}
    """
    put(StringIO(td_nginx), '/etc/nginx/sites-available/teamdashboard', use_sudo=True)
    sudo("ln -s /etc/nginx/sites-available/teamdashboard /etc/nginx/sites-enabled/teamdashboard")

    setup_unicorn()
    sudo('service nginx restart')


def setup_unicorn():
    unicorn_upstart = """#!upstart
start on startup
stop on shutdown

respawn

env USER=www-data
env PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
env RAILS_ENV=production
env GRAPHITE_URL=http://localhost:80
env QUEUE=teamdashboard

#exec start-stop-daemon --make-pidfile --pidfile /var/run/teamdashboard.pid --chuid $USER --start -c app -d /var/www/teamdashboard -x /usr/local/bin/bundle -- exec unicorn -c config/unicorn.rb >> /var/log/teamdashboard/web.log 2>&1"""

    put(StringIO(unicorn_upstart), '/etc/init/teamdashboard.conf', use_sudo=True)
    sudo('mkdir -p /var/log/teamdashboard')
    sudo('chown -R www-data:www-data /var/log/teamdashboard')

    sudo('service teamdashboard restart')


@task
def monitor_all_the_things(PG_DB):
    setup_graphite()
    setup_statsd()
    setup_team_dashboard(PG_DB)

