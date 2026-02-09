# controllers/pda_api.py
from odoo import http, fields
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)


class PdaController(http.Controller):
    """Controlador para manejar dispositivos PDA"""

    @http.route("/api/pda/register", type="json", auth="user", methods=["POST"], csrf=False)
    def register_pda(self, **kwargs):
        """
        Registrar o actualizar un dispositivo PDA

        Parámetros esperados:
        - device_id: ID único del dispositivo
        - device_name: Nombre del dispositivo
        - device_model: Modelo del dispositivo (opcional)
        """
        try:
            device_id = kwargs.get("device_id")
            device_name = kwargs.get("device_name")
            device_model = kwargs.get("device_model", "")
            version_app = kwargs.get("version_app", "")

            if not device_id or not device_name:
                return {"code": 400, "msg": "Faltan parámetros obligatorios: device_id y device_name"}

            # Verificar si el dispositivo ya existe
            pda = request.env["pda.logs"].sudo().search([("device_id", "=", device_id)], limit=1)

            if pda:
                # Actualizar información básica del dispositivo si es necesario
                update_vals = {}
                if pda.device_name != device_name:
                    update_vals["device_name"] = device_name
                if device_model and pda.device_model != device_model:
                    update_vals["device_model"] = device_model
                if version_app and pda.version_app != version_app:
                    update_vals["version_app"] = version_app

                if update_vals:
                    pda.sudo().write(update_vals)

                # Registrar la conexión usando el método específico
                pda.sudo().register_connection(user_id=request.env.user.id, ip_address=request.httprequest.remote_addr, additional_data=kwargs)  # Puedes pasar datos adicionales si los necesitas

                return {
                    "code": 200,
                    "msg": "Dispositivo PDA actualizado y conexión registrada",
                    "data": {
                        "device_id": pda.device_id,
                        "device_name": pda.device_name,
                        "is_authorized": pda.is_authorized,
                        "is_active": pda.is_active,
                        "total_connections": pda.login_count,
                        "monthly_connections": pda.monthly_connections,
                        "device_model": pda.device_model,
                        "version_app": pda.version_app,
                        "needs_authorization": pda.is_authorized == "no",
                    },
                }
            else:
                # Crear un nuevo registro de dispositivo PDA
                new_pda = request.env["pda.logs"].sudo().create({"device_id": device_id, "device_name": device_name, "device_model": device_model, "user_id": request.env.user.id, "ip_address": request.httprequest.remote_addr, "version_app": version_app})

                # Registrar la primera conexión
                new_pda.sudo().register_connection(user_id=request.env.user.id, ip_address=request.httprequest.remote_addr, additional_data=kwargs)

                return {
                    "code": 201,
                    "msg": "Dispositivo PDA creado y primera conexión registrada",
                    "data": {
                        "device_id": new_pda.device_id,
                        "device_name": new_pda.device_name,
                        "is_authorized": new_pda.is_authorized,
                        "is_active": new_pda.is_active,
                        "device_model": new_pda.device_model,
                        "version_app": new_pda.version_app,
                        "needs_authorization": new_pda.is_authorized == "no",
                        "total_connections": new_pda.login_count,
                        "monthly_connections": new_pda.monthly_connections,
                    },
                }

        except Exception as e:
            _logger.error(f"Error registrando PDA: {str(e)}")
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/pda/check", type="json", auth="user", methods=["POST"], csrf=False)
    def check_pda_status(self, **kwargs):
        """
        Verificar estado de autorización de un dispositivo PDA

        Parámetros:
        - device_id: ID del dispositivo
        """
        try:
            device_id = kwargs.get("device_id")

            if not device_id:
                return {"code": 400, "msg": "Parámetro device_id es obligatorio"}

            pda = request.env["pda.logs"].sudo().search([("device_id", "=", device_id)], limit=1)

            if not pda:
                return {"code": 404, "msg": "Dispositivo no encontrado"}

            # Actualizar última actividad
            pda.sudo().write({"last_login": request.env["ir.fields"].Datetime.now(), "login_count": pda.login_count + 1, "user_id": request.env.user.id, "ip_address": request.httprequest.remote_addr})

            is_authorized = pda.is_authorized == "yes" and pda.is_active

            return {
                "code": 200,
                "msg": "Estado verificado correctamente",
                "data": {
                    "device_id": pda.device_id,
                    "device_name": pda.device_name,
                    "device_model": pda.device_model,
                    "is_authorized": is_authorized,
                    "is_active": pda.is_active,
                    "authorization_status": pda.is_authorized,
                    "last_login": pda.last_login.isoformat() if pda.last_login else None,
                    "login_count": pda.login_count,
                },
            }

        except Exception as e:
            _logger.error(f"Error verificando PDA: {str(e)}")
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/pda/list", type="json", auth="user", methods=["POST"], csrf=False)
    def list_pdas(self, **kwargs):
        """
        Listar dispositivos PDA (requiere permisos de administrador)

        Parámetros opcionales:
        - authorized_only: Solo autorizados (true/false)
        - active_only: Solo activos (true/false)
        - limit: Límite de registros (default: 100)
        """
        try:
            # Verificar permisos
            if not request.env.user.has_group("base.group_system"):
                return {"code": 403, "msg": "Permisos insuficientes"}

            authorized_only = kwargs.get("authorized_only", False)
            active_only = kwargs.get("active_only", False)
            limit = kwargs.get("limit", 100)

            domain = []

            if authorized_only:
                domain.append(("is_authorized", "=", "yes"))

            if active_only:
                domain.append(("is_active", "=", True))

            pdas = request.env["pda.logs"].sudo().search(domain, limit=limit, order="last_login desc")

            pda_list = []
            for pda in pdas:
                pda_list.append(
                    {
                        "id": pda.id,
                        "device_id": pda.device_id,
                        "device_name": pda.device_name,
                        "device_model": pda.device_model,
                        "is_authorized": pda.is_authorized,
                        "is_active": pda.is_active,
                        "last_login": pda.last_login.isoformat() if pda.last_login else None,
                        "login_count": pda.login_count,
                        "user_name": pda.user_id.name if pda.user_id else None,
                        "ip_address": pda.ip_address,
                    }
                )

            return {"code": 200, "msg": "Lista obtenida correctamente", "data": {"pdas": pda_list, "total": len(pda_list)}}

        except Exception as e:
            _logger.error(f"Error listando PDAs: {str(e)}")
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/pda/authorize", type="json", auth="user", methods=["POST"], csrf=False)
    def authorize_pda(self, **kwargs):
        """
        Autorizar o desautorizar un dispositivo PDA

        Parámetros:
        - device_id: ID del dispositivo
        - authorize: true para autorizar, false para desautorizar
        """
        try:
            # Verificar permisos
            if not request.env.user.has_group("base.group_system"):
                return {"code": 403, "msg": "Permisos insuficientes"}

            device_id = kwargs.get("device_id")
            authorize = kwargs.get("authorize", True)

            if not device_id:
                return {"code": 400, "msg": "Parámetro device_id es obligatorio"}

            pda = request.env["pda.logs"].sudo().search([("device_id", "=", device_id)], limit=1)

            if not pda:
                return {"code": 404, "msg": "Dispositivo no encontrado"}

            if authorize:
                pda.sudo().action_authorize_device()
                message = f"Dispositivo {pda.device_name} autorizado correctamente"
            else:
                pda.sudo().action_revoke_device()
                message = f"Dispositivo {pda.device_name} desautorizado correctamente"

            return {"code": 200, "msg": message, "data": {"device_id": pda.device_id, "device_name": pda.device_name, "is_authorized": pda.is_authorized, "is_active": pda.is_active}}

        except Exception as e:
            _logger.error(f"Error autorizando PDA: {str(e)}")
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/pda/heartbeat", type="json", auth="user", methods=["POST"], csrf=False)
    def pda_heartbeat(self, **kwargs):
        """
        Mantener dispositivo activo (heartbeat)

        Parámetros:
        - device_id: ID del dispositivo
        - activity_data: Datos adicionales (opcional)
        """
        try:
            device_id = kwargs.get("device_id")
            activity_data = kwargs.get("activity_data", {})

            if not device_id:
                return {"code": 400, "msg": "Parámetro device_id es obligatorio"}

            pda = request.env["pda.logs"].sudo().search([("device_id", "=", device_id)], limit=1)

            if not pda:
                return {"code": 404, "msg": "Dispositivo no encontrado"}

            # Actualizar actividad
            pda.sudo().write({"last_login": request.env["ir.fields"].Datetime.now(), "user_id": request.env.user.id, "ip_address": request.httprequest.remote_addr})

            is_authorized = pda.is_authorized == "yes" and pda.is_active

            return {"code": 200, "msg": "Heartbeat registrado correctamente", "data": {"device_id": pda.device_id, "is_authorized": is_authorized, "is_active": pda.is_active, "last_login": pda.last_login.isoformat()}}

        except Exception as e:
            _logger.error(f"Error en heartbeat PDA: {str(e)}")
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/pda/stats", type="json", auth="user", methods=["GET", "POST"], csrf=False)
    def get_pda_stats(self, **kwargs):
        """
        Obtener estadísticas de dispositivos PDA
        """
        try:
            # Verificar permisos
            if not request.env.user.has_group("base.group_system"):
                return {"code": 403, "msg": "Permisos insuficientes"}

            PDALogs = request.env["pda.logs"].sudo()

            stats = {
                "total_devices": PDALogs.search_count([]),
                "authorized_devices": PDALogs.search_count([("is_authorized", "=", "yes")]),
                "active_devices": PDALogs.search_count([("is_active", "=", True)]),
                "pending_authorization": PDALogs.search_count([("is_authorized", "=", "no")]),
            }

            # Top 5 dispositivos por uso
            top_devices = PDALogs.search([], limit=5, order="login_count desc")
            stats["top_devices"] = [{"device_name": pda.device_name, "login_count": pda.login_count, "last_login": pda.last_login.isoformat() if pda.last_login else None} for pda in top_devices]

            return {"code": 200, "msg": "Estadísticas obtenidas correctamente", "data": stats}

        except Exception as e:
            _logger.error(f"Error obteniendo estadísticas: {str(e)}")
            return {"code": 500, "msg": f"Error interno: {str(e)}"}
