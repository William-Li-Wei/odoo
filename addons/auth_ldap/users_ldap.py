##############################################################################
#    
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.     
#
##############################################################################

import ldap
import logging
from ldap.filter import filter_format

import openerp.exceptions
from openerp import tools
from openerp.osv import fields, osv
from openerp import SUPERUSER_ID
from openerp.modules.registry import RegistryManager
_logger = logging.getLogger(__name__)

class CompanyLDAP(osv.osv):
    _name = 'res.company.ldap'
    _order = 'sequence'
    _rec_name = 'ldap_server'

    def get_ldap_dicts(self, cr, ids=None):
        """ 
        Retrieve res_company_ldap resources from the database in dictionary
        format.

        :param list ids: Valid ids of model res_company_ldap. If not \
        specified, process all resources (unlike other ORM methods).
        :return: ldap configurations
        :rtype: list of dictionaries
        """

        if ids:
            id_clause = 'AND id IN (%s)'
            args = [tuple(ids)]
        else:
            id_clause = ''
            args = []
        cr.execute("""
            SELECT id, company, ldap_server, ldap_server_port, ldap_binddn,
                   ldap_password, ldap_filter, ldap_base, "user", create_user,
                   ldap_tls
            FROM res_company_ldap
            WHERE ldap_server != '' """ + id_clause + """ ORDER BY sequence
        """, args)
        return cr.dictfetchall()

    def connect(self, conf):
        """ 
        Connect to an LDAP server specified by an ldap
        configuration dictionary.

        :param dict conf: LDAP configuration
        :return: an LDAP object
        """

        uri = 'ldap://%s:%d' % (conf['ldap_server'],
                                conf['ldap_server_port'])

        connection = ldap.initialize(uri)
        if conf['ldap_tls']:
            connection.start_tls_s()
        return connection

    def authenticate(self, conf, login, password):
        """
        Authenticate a user against the specified LDAP server.

        In order to prevent an unintended 'unauthenticated authentication',
        which is an anonymous bind with a valid dn and a blank password,
        check for empty passwords explicitely (:rfc:`4513#section-6.3.1`)
        
        :param dict conf: LDAP configuration
        :param login: username
        :param password: Password for the LDAP user
        :return: LDAP entry of authenticated user or False
        :rtype: dictionary of attributes
        """

        if not password:
            return False

        entry = False
        filter = filter_format(conf['ldap_filter'], (login,))
        try:
            results = self.query(conf, filter)

            # Get rid of (None, attrs) for searchResultReference replies
            results = [i for i in results if i[0]]
            if results and len(results) == 1:
                dn = results[0][0]
                conn = self.connect(conf)
                conn.simple_bind_s(dn, password)
                conn.unbind()
                entry = results[0]
        except ldap.INVALID_CREDENTIALS:
            return False
        except ldap.LDAPError, e:
            _logger.error('An LDAP exception occurred: %s', e)
        return entry
        
    def query(self, conf, filter, retrieve_attributes=None):
        """ 
        Query an LDAP server with the filter argument and scope subtree.

        Allow for all authentication methods of the simple authentication
        method:

        - authenticated bind (non-empty binddn + valid password)
        - anonymous bind (empty binddn + empty password)
        - unauthenticated authentication (non-empty binddn + empty password)

        .. seealso::
           :rfc:`4513#section-5.1` - LDAP: Simple Authentication Method.

        :param dict conf: LDAP configuration
        :param filter: valid LDAP filter
        :param list retrieve_attributes: LDAP attributes to be retrieved. \
        If not specified, return all attributes.
        :return: ldap entries
        :rtype: list of tuples (dn, attrs)

        """

        results = []
        try:
            conn = self.connect(conf)
            conn.simple_bind_s(conf['ldap_binddn'] or '',
                               conf['ldap_password'] or '')
            results = conn.search_st(conf['ldap_base'], ldap.SCOPE_SUBTREE,
                                     filter, retrieve_attributes, timeout=60)
            conn.unbind()
        except ldap.INVALID_CREDENTIALS:
            _logger.error('LDAP bind failed.')
        except ldap.LDAPError, e:
            _logger.error('An LDAP exception occurred: %s', e)
        return results

    def map_ldap_attributes(self, cr, uid, conf, login, ldap_entry):
        """
        Compose values for a new resource of model res_users,
        based upon the retrieved ldap entry and the LDAP settings.
        
        :param dict conf: LDAP configuration
        :param login: the new user's login
        :param tuple ldap_entry: single LDAP result (dn, attrs)
        :return: parameters for a new resource of model res_users
        :rtype: dict
        """

        values = { 'name': ldap_entry[1]['cn'][0],
                   'login': login,
                   'company_id': conf['company']
                   }
        return values

    def get_company_name_by_dn(self,dn):
        company_name = False
        if dn and 'o=' in dn:
            company_name = dn.split(",")[1][2:]
        return company_name

    def get_user_org_dn(self, conf, ldap_entry):
        dn = False
        uid = ldap_entry[1]['uid'][0] or False
        ldap_base_for_org = 'dc=agrista,dc=com'
        if uid:
            filter = filter_format('uniquemember=uid=%s,ou=people,dc=agrista,dc=com', (uid,))
            query_results = []
            try:
                conn = self.connect(conf)
                conn.simple_bind_s(conf['ldap_binddn'] or '',
                                   conf['ldap_password'] or '')
                query_results = conn.search_st(ldap_base_for_org, ldap.SCOPE_SUBTREE,
                                         filter, None, timeout=60)
            except ldap.INVALID_CREDENTIALS:
                _logger.error('LDAP bind failed.')
            except ldap.LDAPError, e:
                _logger.error('An LDAP exception occurred: %s', e)
            if query_results and len(query_results) == 1:
                dn = query_results[0][0]
        return dn

    def get_user_org_name(self, conf, ldap_entry):
        dn = self.get_user_org_dn(conf, ldap_entry)
        return self.get_company_name_by_dn(dn)
    
    def get_or_create_user(self, cr, uid, conf, login, ldap_entry,
                           context=None):
        """
        Retrieve an active resource of model res_users with the specified
        login. Create the user if it is not initially found.

        :param dict conf: LDAP configuration
        :param login: the user's login
        :param tuple ldap_entry: single LDAP result (dn, attrs)
        :return: res_users id
        :rtype: int
        """
        
        user_id = False
        company_id = False
        login = tools.ustr(login.lower().strip())
        company_name = self.get_user_org_name(conf, ldap_entry)
        if company_name:
            cr.execute("SELECT id, active FROM res_partner WHERE lower(name)=%s", (company_name.lower(),))
            res = cr.fetchone()
            if res:
                if res[1]:
                    company_id = res[0]
        cr.execute("SELECT id, active FROM res_users WHERE lower(login)=%s", (login,))
        res = cr.fetchone()
        if res:
            if res[1]:
                user_id = res[0]
        elif conf['create_user']:
            _logger.debug("Creating new Odoo user \"%s\" from LDAP" % login)
            user_obj = self.pool['res.users']
            values = self.map_ldap_attributes(cr, uid, conf, login, ldap_entry)
            if company_id:
                values['parent_id']= company_id
            else:
                # create company partner
                company_obj = self.pool['res.partner']
                company_values = {
                    'name': company_name,
                    'company_id': conf['company'],
                    'is_company': True,
                    'notify_email': 'always'
                }
                company_id = company_obj.create(cr, SUPERUSER_ID, company_values)
                values['parent_id'] = company_id
            if conf['user']:
                values['active'] = True
                user_id = user_obj.copy(cr, SUPERUSER_ID, conf['user'],
                                        default=values)
            else:
                user_id = user_obj.create(cr, SUPERUSER_ID, values)
        return user_id

    _columns = {
        'sequence': fields.integer('Sequence'),
        'company': fields.many2one('res.company', 'Company', required=True,
            ondelete='cascade'),
        'ldap_server': fields.char('LDAP Server address', required=True),
        'ldap_server_port': fields.integer('LDAP Server port', required=True),
        'ldap_binddn': fields.char('LDAP binddn', 
            help=("The user account on the LDAP server that is used to query "
                  "the directory. Leave empty to connect anonymously.")),
        'ldap_password': fields.char('LDAP password',
            help=("The password of the user account on the LDAP server that is "
                  "used to query the directory.")),
        'ldap_filter': fields.char('LDAP filter', required=True),
        'ldap_base': fields.char('LDAP base', required=True),
        'user': fields.many2one('res.users', 'Template User',
            help="User to copy when creating new users"),
        'create_user': fields.boolean('Create user',
            help="Automatically create local user accounts for new users authenticating via LDAP"),
        'ldap_tls': fields.boolean('Use TLS',
            help="Request secure TLS/SSL encryption when connecting to the LDAP server. "
                 "This option requires a server with STARTTLS enabled, "
                 "otherwise all authentication attempts will fail."),
    }
    _defaults = {
        'ldap_server': '127.0.0.1',
        'ldap_server_port': 389,
        'sequence': 10,
        'create_user': True,
    }



class res_company(osv.osv):
    _inherit = "res.company"
    _columns = {
        'ldaps': fields.one2many(
            'res.company.ldap', 'company', 'LDAP Parameters', copy=True, groups="base.group_system"),
    }


class users(osv.osv):
    _inherit = "res.users"
    def _login(self, db, login, password):
        user_id = super(users, self)._login(db, login, password)
        if user_id:
            return user_id
        registry = RegistryManager.get(db)
        with registry.cursor() as cr:
            cr.execute("SELECT id FROM res_users WHERE lower(login)=%s", (login,))
            res = cr.fetchone()
            if res:
                return False
            ldap_obj = registry.get('res.company.ldap')
            for conf in ldap_obj.get_ldap_dicts(cr):
                entry = ldap_obj.authenticate(conf, login, password)
                if entry:
                    user_id = ldap_obj.get_or_create_user(
                        cr, SUPERUSER_ID, conf, login, entry)
                    if user_id:
                        break
            return user_id

    def check_credentials(self, cr, uid, password):
        try:
            super(users, self).check_credentials(cr, uid, password)
        except openerp.exceptions.AccessDenied:

            cr.execute('SELECT login FROM res_users WHERE id=%s AND active=TRUE',
                       (int(uid),))
            res = cr.fetchone()
            if res:
                ldap_obj = self.pool['res.company.ldap']
                for conf in ldap_obj.get_ldap_dicts(cr):
                    if ldap_obj.authenticate(conf, res[0], password):
                        return
            raise
        
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: