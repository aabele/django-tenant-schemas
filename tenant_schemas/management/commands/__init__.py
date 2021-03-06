import django
from optparse import make_option
from django.conf import settings
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import BaseCommand, NoArgsCommand, CommandError
from django.db import connection, DEFAULT_DB_ALIAS

try:
    from django.utils.six.moves import input
except ImportError:
    input = raw_input
from tenant_schemas.utils import get_tenant_model, get_public_schema_name


class BaseTenantCommand(BaseCommand):
    """
    Generic command class useful for iterating any existing command
    over all schemata. The actual command name is expected in the
    class variable COMMAND_NAME of the subclass.
    """

    def __new__(cls, *args, **kwargs):
        """
        Sets option_list and help dynamically.
        """
        obj = super(BaseTenantCommand, cls).__new__(cls, *args, **kwargs)

        app_name = get_commands()[obj.COMMAND_NAME]
        if isinstance(app_name, BaseCommand):
            # If the command is already loaded, use it directly.
            cmdclass = app_name
        else:
            cmdclass = load_command_class(app_name, obj.COMMAND_NAME)

        if django.VERSION < (1, 8, 0):
            # inherit the options from the original command
            obj.option_list = cmdclass.option_list
            obj.option_list += (
                make_option("-s", "--schema", dest="schema_name"),
            )
            obj.option_list += (
                make_option("-p", "--skip-public", dest="skip_public", action="store_true", default=False),
            )

        # prepend the command's original help with the info about schemata iteration
        obj.help = "Calls %s for all registered schemata. You can use regular %s options. " \
                   "Original help for %s: %s" % (obj.COMMAND_NAME, obj.COMMAND_NAME, obj.COMMAND_NAME,
                                                 getattr(cmdclass, 'help', 'none'))
        return obj

    def add_arguments(self, parser):
        super(BaseTenantCommand, self).add_arguments(parser)
        parser.add_argument("-s", "--schema", dest="schema_name")
        parser.add_argument("-p", "--skip-public", dest="skip_public", action="store_true", default=False)

    def execute_command(self, tenant, command_name, *args, **options):
        verbosity = int(options.get('verbosity'))

        if verbosity >= 1:
            print()
            print(self.style.NOTICE("=== Switching to schema '")
                  + self.style.SQL_TABLE(tenant.schema_name)
                  + self.style.NOTICE("' then calling %s:" % command_name))

        connection.set_tenant(tenant)

        # call the original command with the args it knows
        call_command(command_name, *args, **options)

    def handle(self, *args, **options):
        """
        Iterates a command over all registered schemata.
        """
        if options['schema_name']:
            # only run on a particular schema
            connection.set_schema_to_public()
            self.execute_command(get_tenant_model().objects.get(schema_name=options['schema_name']), self.COMMAND_NAME,
                                 *args, **options)
        else:
            for tenant in get_tenant_model().objects.all():
                if not (options['skip_public'] and tenant.schema_name == get_public_schema_name()):
                    self.execute_command(tenant, self.COMMAND_NAME, *args, **options)


class InteractiveTenantOption(object):
    def __init__(self, *args, **kwargs):
        super(InteractiveTenantOption, self).__init__(*args, **kwargs)
        if django.VERSION < (1, 8, 0):
            self.option_list += (
                make_option("-s", "--schema", dest="schema_name", help="specify tenant schema"),
            )

    def add_arguments(self, parser):
        parser.add_argument("-s", "--schema", dest="schema_name", help="specify tenant schema")

    def get_tenant_from_options_or_interactive(self, **options):
        TenantModel = get_tenant_model()
        all_tenants = TenantModel.objects.all()

        if not all_tenants:
            raise CommandError("""There are no tenants in the system.
To learn how create a tenant, see:
https://django-tenant-schemas.readthedocs.org/en/latest/use.html#creating-a-tenant""")

        if options.get('schema_name'):
            tenant_schema = options['schema_name']
        else:
            while True:
                tenant_schema = input("Enter Tenant Schema ('?' to list schemas): ")
                if tenant_schema != '?':
                    break
                print('\n'.join(["%s - %s" % (t.schema_name, ', '.join(t.get_domains())) for t in all_tenants]))

        if tenant_schema not in [t.schema_name for t in all_tenants]:
            raise CommandError("Invalid tenant schema, '%s'" % (tenant_schema,))

        return TenantModel.objects.get(schema_name=tenant_schema)


class TenantWrappedCommand(InteractiveTenantOption, BaseCommand):
    """
    Generic command class useful for running any existing command
    on a particular tenant. The actual command name is expected in the
    class variable COMMAND_NAME of the subclass.
    """

    def __new__(cls, *args, **kwargs):
        obj = super(TenantWrappedCommand, cls).__new__(cls, *args, **kwargs)
        obj.command_instance = obj.COMMAND()
        obj.option_list = obj.command_instance.option_list
        return obj

    def add_arguments(self, parser):
        super(TenantWrappedCommand, self).add_arguments(parser)
        self.command_instance.add_arguments(parser)

    def handle(self, *args, **options):
        tenant = self.get_tenant_from_options_or_interactive(**options)
        connection.set_tenant(tenant)

        self.command_instance.execute(*args, **options)


class SyncCommon(BaseCommand):
    if django.VERSION < (1, 8, 0):
        option_list = (
            make_option('--tenant', action='store_true', dest='tenant', default=False,
                        help='Tells Django to populate only tenant applications.'),
            make_option('--shared', action='store_true', dest='shared', default=False,
                        help='Tells Django to populate only shared applications.'),
            make_option('--app_label', action='store', dest='app_label', nargs='?',
                        help='App label of an application to synchronize the state.'),
            make_option('--migration_name', action='store', dest='migration_name', nargs='?',
                        help=('Database state will be brought to the state after that '
                              'migration. Use the name "zero" to unapply all migrations.')),
            make_option("-s", "--schema", dest="schema_name"),
        )

    def __init__(self, stdout=None, stderr=None, no_color=False):
        if django.VERSION >= (1, 8, 0):
            super(SyncCommon, self).__init__(stdout, stderr, no_color)
        else:
            super(SyncCommon, self).__init__()

    def add_arguments(self, parser):
        # for django 1.8 and above
        parser.add_argument('--tenant', action='store_true', dest='tenant', default=False,
                    help='Tells Django to populate only tenant applications.')
        parser.add_argument('--shared', action='store_true', dest='shared', default=False,
                    help='Tells Django to populate only shared applications.')
        parser.add_argument('--app_label', action='store', dest='app_label', nargs='?',
                    help='App label of an application to synchronize the state.')
        parser.add_argument('--migration_name', action='store', dest='migration_name', nargs='?',
                    help=('Database state will be brought to the state after that '
                          'migration. Use the name "zero" to unapply all migrations.'))
        parser.add_argument("-s", "--schema", dest="schema_name")

    def handle(self, *args, **options):
        self.sync_tenant = options.get('tenant')
        self.sync_public = options.get('shared')
        self.schema_name = options.get('schema_name')
        self.installed_apps = settings.INSTALLED_APPS
        self.args = args
        self.options = options

        if self.schema_name:
            if self.sync_public:
                raise CommandError("schema should only be used with the --tenant switch.")
            elif self.schema_name == get_public_schema_name():
                self.sync_public = True
            else:
                self.sync_tenant = True
        elif not self.sync_public and not self.sync_tenant:
            # no options set, sync both
            self.sync_tenant = True
            self.sync_public = True

        if hasattr(settings, 'TENANT_APPS'):
            self.tenant_apps = settings.TENANT_APPS
        if hasattr(settings, 'SHARED_APPS'):
            self.shared_apps = settings.SHARED_APPS

    def _notice(self, output):
        if int(self.options.get('verbosity', 1)) >= 1:
            self.stdout.write(self.style.NOTICE(output))
