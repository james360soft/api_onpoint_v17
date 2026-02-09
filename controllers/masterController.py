# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, date
import json
import base64


class MasterData(http.Controller):

    ## GET Configuraciones
    @http.route("/api/configurations", auth="user", type="json", methods=["GET"])
    def get_configurations(self):
        try:
            # Obtener configuración general
            config = request.env["appwms.config.general"].sudo().search([], limit=1)
            config_data = {"muelle_option": config.muelle_option if config else None}

            # Obtener datos del usuario autenticado
            user = request.env.user
            user_data = {
                "name": user.name,
                "id": user.id,
                "last_name": user.name,
                "email": user.email,
            }

            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            user_data["allowed_warehouses"] = []

            for warehouse in allowed_warehouses:
                if warehouse:
                    user_data["allowed_warehouses"].append(
                        {
                            "id": warehouse.id,
                            "name": warehouse.name,
                        }
                    )

            # Verificar permisos en appwms.users_wms
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)
            if not user_wms:
                return {
                    "code": 401,
                    "msg": "El usuario no tiene permisos en el módulo de configuraciones en Odoo",
                }

            user_permissions = (
                request.env["appwms.user_permission_app"].sudo().search([("user_id", "=", user.id)], limit=1)
            )
            if not user_permissions:
                return {
                    "code": 401,
                    "msg": "El usuario no tiene permisos específicos asignados",
                }

            show_photo_temperature = request.env["appwms.temperature"].sudo().search([], limit=1)
            if show_photo_temperature:
                user_data["show_photo_temperature"] = show_photo_temperature.show_photo_temperature
            else:
                user_data["show_photo_temperature"] = False

            config_returns = request.env["config.returns.general"].sudo().search([], limit=1)
            if config_returns:
                user_data["returns_location_dest_option"] = config_returns.location_option
            else:
                user_data["returns_location_dest_option"] = "predefined"

            # Construir respuesta final
            response_data = {
                **user_data,
                "rol": user_wms.user_rol if user_wms.user_rol else "USER",
                "muelle_option": config_data.get("muelle_option"),
                "location_picking_manual": getattr(user_permissions, "location_picking_manual", False),
                "manual_product_selection": getattr(user_permissions, "manual_product_selection", False),
                "manual_quantity": getattr(user_permissions, "manual_quantity", False),
                "manual_spring_selection": getattr(user_permissions, "manual_spring_selection", False),
                "show_detalles_picking": getattr(user_permissions, "show_detalles_picking", False),
                "show_next_locations_in_details": getattr(
                    user_permissions, "show_next_locations_in_details", False
                ),
                "location_pack_manual": getattr(user_permissions, "location_pack_manual", False),
                "show_detalles_pack": getattr(user_permissions, "show_detalles_pack", False),
                "show_next_locations_in_details_pack": getattr(
                    user_permissions, "show_next_locations_in_details_pack", False
                ),
                "manual_product_selection_pack": getattr(
                    user_permissions, "manual_product_selection_pack", False
                ),
                "manual_quantity_pack": getattr(user_permissions, "manual_quantity_pack", False),
                "manual_spring_selection_pack": getattr(
                    user_permissions, "manual_spring_selection_pack", False
                ),
                "scan_product": getattr(user_permissions, "scan_product", False),
                "allow_move_excess": getattr(user_permissions, "allow_move_excess", False),
                "hide_expected_qty": getattr(user_permissions, "hide_expected_qty", False),
                "manual_product_reading": getattr(user_permissions, "manual_product_reading", False),
                "manual_source_location": getattr(user_permissions, "manual_source_location", False),
                "show_owner_field": getattr(user_permissions, "show_owner_field", False),
                "hide_validate_reception": getattr(user_permissions, "hide_validate_reception", False),
                "scan_destination_location_reception": getattr(
                    user_permissions, "scan_destination_location_reception", False
                ),
                "manual_product_selection_transfer": getattr(
                    user_permissions, "manual_product_selection_transfer", False
                ),
                "manual_source_location_transfer": getattr(
                    user_permissions, "manual_source_location_transfer", False
                ),
                "manual_dest_location_transfer": getattr(
                    user_permissions, "manual_dest_location_transfer", False
                ),
                "manual_quantity_transfer": getattr(user_permissions, "manual_quantity_transfer", False),
                "hide_validate_transfer": getattr(user_permissions, "hide_validate_transfer", False),
                "count_quantity_inventory": getattr(user_permissions, "count_quantity_inventory", False),
                "update_item_inventory": getattr(user_permissions, "update_item_inventory", False),
                "update_location_inventory": getattr(user_permissions, "update_location_inventory", False),
                "manual_product_selection_inventory": getattr(
                    user_permissions, "manual_product_selection_inventory", False
                ),
                "location_manual_inventory": getattr(user_permissions, "location_manual_inventory", False),
                "hide_validate_picking": getattr(user_permissions, "hide_validate_picking", False),
                "hide_validate_packing": getattr(user_permissions, "hide_validate_packing", False),
                "access_production_module": getattr(user_permissions, "access_production_module", False),
                "allow_move_excess_production": getattr(
                    user_permissions, "allow_move_excess_production", False
                ),
            }

            return {"code": 200, "result": response_data}

        except AccessError as e:
            return {"code": 403, "msg": "Acceso denegado: {}".format(str(e))}
        except Exception as err:
            return {"code": 400, "msg": "Error inesperado: {}".format(str(err))}

            # return {"status": "error", "message": str(e)}

    ## GET Muelles
    @http.route("/api/muelles", auth="user", type="json", methods=["GET"])
    def get_muelles(self):
        try:
            # Obtener todos los muelles con las condiciones especificadas
            muelles = (
                request.env["stock.location"]
                .sudo()
                .search(
                    [
                        ("usage", "=", "internal"),
                        ("is_a_dock", "=", True),
                        ("is_full", "=", False),
                    ]
                )
            )

            array_muelles = []

            for muelle in muelles:
                array_muelles.append(
                    {
                        "id": muelle.id,
                        "name": muelle.name,
                        "complete_name": muelle.complete_name,
                        "location_id": (muelle.location_id.id if muelle.location_id else None),
                        "barcode": muelle.barcode or "",
                    }
                )

            return {"code": 200, "result": array_muelles}

        except AccessError as e:
            return {"code": 403, "msg": "Acceso denegado: {}".format(str(e))}
        except Exception as err:
            return {"code": 400, "msg": "Error inesperado: {}".format(str(err))}

    ## GET Terceros
    @http.route("/api/terceros", auth="user", type="json", methods=["GET"])
    def get_terceros(self):
        try:
            # Obtener todos los maestros
            maestros = request.env["res.partner"].search(
                [("active", "=", True)]
            )  # Solo empresas, ajusta según necesites  # Solo registros activos

            array_maestros = []

            for maestro in maestros:
                array_maestros.append(
                    {
                        "id": maestro.id,
                        "document": maestro.vat or "",
                        "sucursal": maestro.sucursal or "",
                        "name": maestro.name,
                        "email": maestro.email or "",
                        "phone": maestro.phone or "",
                    }
                )

            return {"code": 200, "result": array_maestros}

        except AccessError as e:
            return {"code": 403, "msg": "Acceso denegado: {}".format(str(e))}
        except Exception as err:
            return {"code": 400, "msg": "Error inesperado: {}".format(str(err))}

    ## Obtener almacenes
    @http.route("/api/warehouses", auth="user", type="json", methods=["GET"])
    def get_warehouses(self):
        try:
            # Buscar todos los registros del modelo 'stock.warehouse'
            warehouses = request.env["stock.warehouse"].search([])

            array_warehouses = []

            for warehouse in warehouses:
                array_warehouses.append(
                    {
                        "id": warehouse.id,
                        "name": warehouse.name,
                        "code": warehouse.code or "",  # El código interno del almacén
                        "company_id": (warehouse.company_id.id if warehouse.company_id else False),
                        "company_name": (warehouse.company_id.name if warehouse.company_id else ""),
                    }
                )

            return {"code": 200, "result": array_warehouses}

        except AccessError as e:
            return {"code": 403, "msg": "Acceso denegado: {}".format(str(e))}
        except Exception as err:
            return {"code": 400, "msg": "Error inesperado: {}".format(str(err))}

    ## GET Novedades de Picking
    @http.route("/api/picking_novelties", auth="user", type="json", methods=["GET"])
    def get_picking_novelties(self):
        try:
            # Obtener todas las novedades de picking
            picking_novelties = request.env["picking.novelties"].sudo().search([])

            array_picking_novelties = []

            for novelty in picking_novelties:
                array_picking_novelties.append(
                    {
                        "id": novelty.id,
                        "name": novelty.name,
                        "code": novelty.code,
                    }
                )

            return {"code": 200, "result": array_picking_novelties}

        except AccessError as e:
            return {"code": 403, "msg": "Acceso denegado: {}".format(str(e))}
        except Exception as err:
            return {"code": 400, "msg": "Error inesperado: {}".format(str(err))}

    # POST Check de sesión
    @http.route("/custom/session_check", type="json", auth="none", csrf=False)
    def session_check(self):
        uid = request.session.uid
        if uid:
            return {
                "active": True,
                "uid": uid,
                "user": request.env["res.users"].sudo().browse(uid).name,
            }
        else:
            return {"active": False}

    ## POST Tiempo de inicio de Picking
    @http.route("/api/update_start_time", auth="user", type="json", methods=["POST"])
    def post_picking_start_time(self, picking_id, start_time, field_name):
        try:
            # Buscar el picking
            picking = request.env["stock.picking.batch"].sudo().search([("id", "=", picking_id)], limit=1)

            if not picking:
                return {
                    "code": 404,
                    "msg": "No se encontró el picking con el ID proporcionado",
                }

            # Validar start_time
            if not start_time:
                return {"code": 400, "msg": "El tiempo 'start_time' es requerido"}

            # Convertir start_time a datetime para validaciones
            try:
                start_time_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return {
                    "code": 400,
                    "msg": "Formato de start_time inválido. Debe ser 'YYYY-MM-DD HH:MM:SS'",
                }

            if picking[field_name]:
                # Validar que el campo ya tenga un valor
                return {
                    "code": 400,
                    "msg": f"El campo '{field_name}' ya tiene un valor registrado",
                }

            # Guardar start_time
            picking.sudo().write({field_name: start_time_dt})

            return {"code": 200, "msg": "Tiempo de inicio actualizado correctamente"}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado {str(err)}"}

    ## POST Tiempo de finalización de Picking
    @http.route("/api/update_end_time", auth="user", type="json", methods=["POST"])
    def post_picking_end_time(self, picking_id, end_time, field_name):
        try:
            # Buscar el picking batch
            picking = request.env["stock.picking.batch"].sudo().search([("id", "=", picking_id)], limit=1)

            if not picking:
                return {
                    "code": 404,
                    "msg": "No se encontró el picking con el ID proporcionado",
                }

            # Validar end_time
            if not end_time:
                return {"code": 400, "msg": "El tiempo 'end_time' es requerido"}

            # Convertir end_time a datetime
            try:
                end_time_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return {
                    "code": 400,
                    "msg": "Formato de end_time inválido. Debe ser 'YYYY-MM-DD HH:MM:SS'",
                }

            # Validar que end_time no sea en el futuro
            # if end_time_dt > datetime.now():
            #     return {"code": 400, "msg": "end_time no puede ser en el futuro"}

            # Obtener el nombre del campo de inicio correspondiente
            field_name_start = field_name.replace("end_", "start_")

            # Validar que el campo start_time correspondiente ya esté registrado
            start_time_str = getattr(picking, field_name_start, None)
            if not start_time_str:
                return {
                    "code": 400,
                    "msg": f"No se puede registrar '{field_name}' sin un '{field_name_start}' previo",
                }

            # Convertir start_time a datetime
            start_time_dt = datetime.strptime(str(start_time_str), "%Y-%m-%d %H:%M:%S")

            # Validar que end_time sea mayor que start_time
            if end_time_dt <= start_time_dt:
                return {
                    "code": 400,
                    "msg": f"'{field_name}' debe ser mayor que '{field_name_start}'",
                }

            # Guardar end_time
            picking.sudo().write({field_name: end_time_dt})

            return {"code": 200, "msg": f"{field_name} actualizado correctamente"}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado {str(err)}"}

    ## POST Tiempo de inicio de batch por usuario
    @http.route("/api/start_time_batch_user", auth="user", type="json", methods=["POST"])
    def post_start_time_batch_user(self, **auth):
        try:
            # Validar campos requeridos
            required_fields = ["id_batch", "start_time", "user_id", "operation_type"]
            for field in required_fields:
                if not auth.get(field):
                    return {"code": 400, "msg": f"El campo '{field}' es requerido"}

            batch_id = auth.get("id_batch")
            user_id = auth.get("user_id")
            operation_type = auth.get("operation_type")

            # Buscar el Batch
            batch = request.env["stock.picking.batch"].sudo().search([("id", "=", batch_id)], limit=1)
            if not batch:
                return {
                    "code": 404,
                    "msg": f"No se encontró el BATCH con ID {batch_id}",
                }

            # Buscar el Usuario
            user = request.env["res.users"].sudo().search([("id", "=", user_id)], limit=1)
            if not user:
                return {
                    "code": 404,
                    "msg": f"No se encontró el usuario con ID {user_id}",
                }

            # Convertir start_time a datetime
            try:
                start_time = datetime.strptime(auth.get("start_time"), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return {
                    "code": 400,
                    "msg": "Formato de 'start_time' inválido. Debe ser 'YYYY-MM-DD HH:MM:SS'",
                }

            # Validar que no exista un registro duplicado
            existing_time = (
                request.env["batch.user.time"]
                .sudo()
                .search(
                    [
                        ("batch_id", "=", batch.id),
                        ("user_id", "=", user.id),
                        ("operation_type", "=", operation_type),
                        ("start_time", "!=", False),
                    ],
                    limit=1,
                )
            )

            if existing_time:
                return {
                    "code": 400,
                    "msg": "Ya existe un registro con los mismos datos",
                }

            # Crear el registro
            new_record = (
                request.env["batch.user.time"]
                .sudo()
                .create(
                    {
                        "batch_id": batch.id,
                        "user_id": user.id,
                        "operation_type": operation_type,
                        "start_time": start_time,
                    }
                )
            )

            return {
                "code": 200,
                "msg": "Registro creado con éxito",
                "data": {
                    "id": new_record.id,
                    "batch_id": new_record.batch_id.id,
                    "user_id": new_record.user_id.id,
                    "operation_type": new_record.operation_type,
                    "start_time": new_record.start_time,
                },
            }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Tiempo de fin de batch por usuario
    @http.route("/api/end_time_batch_user", auth="user", type="json", methods=["POST"])
    def post_end_time_batch_user(self, **auth):
        try:
            # Validar campos requeridos
            required_fields = ["id_batch", "end_time", "user_id", "operation_type"]
            for field in required_fields:
                if not auth.get(field):
                    return {"code": 400, "msg": f"El campo '{field}' es requerido"}

            batch_id = auth.get("id_batch")
            user_id = auth.get("user_id")
            operation_type = auth.get("operation_type")

            # Buscar el Batch
            batch = request.env["stock.picking.batch"].sudo().search([("id", "=", batch_id)], limit=1)
            if not batch:
                return {
                    "code": 404,
                    "msg": f"No se encontró el BATCH con ID {batch_id}",
                }

            # Buscar el Usuario
            user = request.env["res.users"].sudo().search([("id", "=", user_id)], limit=1)
            if not user:
                return {
                    "code": 404,
                    "msg": f"No se encontró el usuario con ID {user_id}",
                }

            # Convertir end_time a datetime
            try:
                end_time = datetime.strptime(auth.get("end_time"), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return {
                    "code": 400,
                    "msg": "Formato de 'end_time' inválido. Debe ser 'YYYY-MM-DD HH:MM:SS'",
                }

            # Validar que no exista un registro duplicado
            existing_time = (
                request.env["batch.user.time"]
                .sudo()
                .search(
                    [
                        ("batch_id", "=", batch.id),
                        ("user_id", "=", user.id),
                        ("operation_type", "=", operation_type),
                    ],
                    limit=1,
                )
            )

            if existing_time:
                # actualizar el registro existente
                existing_time.write({"end_time": end_time})
                return {
                    "code": 200,
                    "msg": "Registro actualizado con éxito",
                    "data": {
                        "id": existing_time.id,
                        "batch_id": existing_time.batch_id.id,
                        "user_id": existing_time.user_id.id,
                        "operation_type": existing_time.operation_type,
                        "end_time": existing_time.end_time,
                    },
                }

            else:
                return {
                    "code": 404,
                    "msg": "No se encontró un registro con los datos proporcionados",
                }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Version de la app
    @http.route("/api/create-version", auth="user", type="json", methods=["POST"])
    def post_version(self, **auth):
        try:
            # Validar campos requeridos
            required_fields = ["version"]
            for field in required_fields:
                if not auth.get(field):
                    return {"code": 400, "msg": f"El campo '{field}' es requerido"}

            # Procesar las notas como una lista
            notes = auth.get("notes", [])
            if not isinstance(notes, list):
                notes = ["Sin notas"]

            # Serializar las notas a formato JSON
            notes_json = json.dumps(notes)

            # Crear el registro con la fecha actual
            new_record = (
                request.env["app.version"]
                .sudo()
                .create(
                    {
                        "version": auth.get("version"),
                        "release_date": auth.get(
                            "release_date", date.today()
                        ),  # Usa la fecha actual si no se envía
                        "notes": notes_json,  # Almacena las notas como JSON
                        "url_download": auth.get("url_download", ""),  # Puede estar vacío
                    }
                )
            )

            # Para la respuesta, devuelve las notas como lista
            return {
                "code": 200,
                "msg": "Registro creado con éxito",
                "data": {
                    "id": new_record.id,
                    "version": new_record.version,
                    "release_date": str(new_record.release_date),
                    "notes": json.loads(new_record.notes),  # Convierte de vuelta a lista
                    "url_download": new_record.url_download,
                },
            }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Versiones de la app
    @http.route("/api/versions", auth="user", type="json", methods=["GET"])
    def get_versions(self):
        try:
            # Obtener todas las versiones
            versions = request.env["app.version"].sudo().search([])

            array_versions = []

            for version in versions:
                array_versions.append(
                    {
                        "id": version.id,
                        "version": version.version,
                        "release_date": str(version.release_date),
                        "notes": version.notes,
                        "url_download": version.url_download,
                    }
                )

            return {"code": 200, "result": array_versions}

        except AccessError as e:
            return {"code": 403, "msg": "Acceso denegado: {}".format(str(e))}
        except Exception as err:
            return {"code": 400, "msg": "Error inesperado: {}".format(str(err))}

    ## GET Ultima version de la app
    @http.route("/api/last-version", auth="user", type="json", methods=["GET"])
    def get_last_version(self):
        try:
            # Obtener la última versión
            last_version = request.env["app.version"].sudo().search([], order="id desc", limit=1)

            if not last_version:
                return {"code": 404, "msg": "No se encontró ninguna versión"}

            # Convertir el texto JSON a una lista Python
            notes_list = []
            if last_version.notes:
                try:
                    notes_list = json.loads(last_version.notes)
                except:
                    notes_list = ["Error al procesar las notas"]

            return {
                "code": 200,
                "result": {
                    "id": last_version.id,
                    "version": last_version.version,
                    "release_date": str(last_version.release_date),
                    "notes": notes_list,  # Ahora devuelve la lista en lugar del string JSON
                    "url_download": last_version.url_download,
                },
            }

        except AccessError as e:
            return {"code": 403, "msg": "Acceso denegado: {}".format(str(e))}
        except Exception as err:
            return {"code": 400, "msg": "Error inesperado: {}".format(str(err))}

    ## Eliminar version de la app
    @http.route("/api/delete-version", auth="user", type="json", methods=["POST"])
    def delete_version(self, version_id):
        try:
            # Buscar la versión
            version = request.env["app.version"].sudo().search([("id", "=", version_id)], limit=1)

            if not version:
                return {
                    "code": 404,
                    "msg": "No se encontró la versión con el ID proporcionado",
                }

            # Eliminar la versión
            version.unlink()

            return {"code": 200, "msg": "Versión eliminada correctamente"}

        except AccessError as e:
            return {"code": 403, "msg": "Acceso denegado: {}".format(str(e))}
        except Exception as err:
            return {"code": 400, "msg": "Error inesperado: {}".format(str(err))}

    ## POST Update tiempo de recepcion
    @http.route("/api/update_time_reception", auth="user", type="json", methods=["POST"])
    def post_reception_start_time(self, reception_id, time, field_name):
        try:
            # Buscar la recepción
            reception = request.env["stock.picking"].sudo().search([("id", "=", reception_id)], limit=1)

            if not reception:
                return {
                    "code": 404,
                    "msg": "No se encontró la recepción con el ID proporcionado",
                }

            # Validar start_time
            if not time:
                return {"code": 400, "msg": "El tiempo 'start_time' es requerido"}

            # Convertir start_time a datetime para validaciones
            try:
                start_time_dt = datetime.strptime(time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return {
                    "code": 400,
                    "msg": "Formato de start_time inválido. Debe ser 'YYYY-MM-DD HH:MM:SS'",
                }

            # Validar que el start_time no sea en el futuro
            # if start_time_dt > datetime.now():
            #     return {"code": 400, "msg": "start_time no puede ser en el futuro"}

            # Guardar start_time
            reception.sudo().write({field_name: start_time_dt})

            if "start" in field_name:
                return {
                    "code": 200,
                    "msg": "Tiempo de inicio actualizado correctamente",
                }
            else:
                return {"code": 200, "msg": "Tiempo de fin actualizado correctamente"}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado {str(err)}"}

    ## POST Update tiempo de transferencia
    @http.route("/api/update_time_transfer", auth="user", type="json", methods=["POST"])
    def post_transfer_start_time(self, transfer_id, time, field_name):
        try:
            # Buscar la transferencia
            transfer = request.env["stock.picking"].sudo().search([("id", "=", transfer_id)], limit=1)

            if not transfer:
                return {
                    "code": 404,
                    "msg": "No se encontró la transferencia con el ID proporcionado",
                }

            # Validar start_time
            if not time:
                return {"code": 400, "msg": "El tiempo 'start_time' es requerido"}

            # Convertir start_time a datetime para validaciones
            try:
                start_time_dt = datetime.strptime(time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return {
                    "code": 400,
                    "msg": "Formato de start_time inválido. Debe ser 'YYYY-MM-DD HH:MM:SS'",
                }

            # Validar que el start_time no sea en el futuro
            # if start_time_dt > datetime.now():
            #     return {"code": 400, "msg": "start_time no puede ser en el futuro"}

            # Guardar start_time
            transfer.sudo().write({field_name: start_time_dt})

            if "start" in field_name:
                return {
                    "code": 200,
                    "msg": "Tiempo de inicio actualizado correctamente",
                }
            else:
                return {"code": 200, "msg": "Tiempo de fin actualizado correctamente"}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado {str(err)}"}

    ## POST PARA VACIAR O LLENAR UN MUELLE
    @http.route("/api/update_dock", auth="user", type="json", methods=["POST"])
    def post_update_dock(self, **auth):
        try:
            dock_id = auth.get("muelle_id", 0)
            is_full = auth.get("is_full", False)

            # Buscar el muelle
            dock = request.env["stock.location"].sudo().search([("id", "=", dock_id)], limit=1)
            if not dock:
                return {
                    "code": 404,
                    "msg": f"No se encontró el muelle con ID {dock_id}",
                }

            # Actualizar el estado del muelle
            dock.sudo().write({"is_full": is_full})

            if is_full:
                msg = "El muelle se ha llenado correctamente"
            else:
                msg = "El muelle se ha vaciado correctamente"

            return {"code": 200, "msg": msg}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Actualizar producto
    @http.route("/api/update_product", auth="user", type="json", methods=["POST"], csrf=False)
    def post_update_product(self, **auth):
        """
        Actualiza información de un producto en el WMS y devuelve información completa

        Parámetros esperados:
        - product_id: ID del producto a actualizar
        - name: Nombre del producto (opcional)
        - default_code: Código interno del producto (opcional)
        - barcode: Código de barras (opcional)
        - categ_id: ID de la categoría (opcional)
        - list_price: Precio de venta (opcional)
        - standard_price: Costo del producto (opcional)
        - weight: Peso del producto (opcional)
        - volume: Volumen del producto (opcional)
        - active: Estado activo/inactivo (opcional)
        """
        try:
            # Obtener datos del request
            product_id = auth.get("product_id")
            name = auth.get("name")
            default_code = auth.get("default_code")
            barcode = auth.get("barcode")
            categ_id = auth.get("categ_id")
            list_price = auth.get("list_price")
            standard_price = auth.get("standard_price")
            weight = auth.get("weight")
            volume = auth.get("volume")
            description = auth.get("description")
            active = auth.get("active")

            # Validar que se proporcione el ID del producto
            if not product_id:
                return {"code": 400, "msg": "El campo 'product_id' es requerido"}

            # Buscar el producto
            product = request.env["product.product"].sudo().browse(product_id)

            if not product.exists():
                return {
                    "code": 404,
                    "msg": f"No se encontró el producto con ID: {product_id}",
                }

            # Preparar datos para actualizar
            update_data = {}

            # Agregar campos solo si vienen en el request y no están vacíos
            if name is not None and str(name).strip():
                update_data["name"] = name.strip()
            if default_code is not None and str(default_code).strip():
                update_data["default_code"] = default_code.strip()
            if barcode is not None and str(barcode).strip():
                update_data["barcode"] = barcode.strip()
            if categ_id is not None and categ_id:
                update_data["categ_id"] = categ_id
            if list_price is not None and list_price != "":
                update_data["list_price"] = list_price
            if standard_price is not None and standard_price != "":
                update_data["standard_price"] = standard_price
            if weight is not None and weight != "":
                update_data["weight"] = weight
            if volume is not None and volume != "":
                update_data["volume"] = volume
            if description is not None and str(description).strip():
                update_data["description"] = description.strip()
            if active is not None and active != "":
                update_data["active"] = active

            # Validaciones específicas
            if update_data.get("barcode"):
                # Verificar que el código de barras no esté duplicado
                existing_barcode = (
                    request.env["product.product"]
                    .sudo()
                    .search(
                        [
                            ("barcode", "=", update_data["barcode"]),
                            ("id", "!=", product_id),
                        ]
                    )
                )
                if existing_barcode:
                    return {
                        "code": 400,
                        "msg": f"El código de barras '{update_data['barcode']}' ya existe en otro producto",
                    }

            if update_data.get("default_code"):
                # Verificar que el código interno no esté duplicado
                existing_code = (
                    request.env["product.product"]
                    .sudo()
                    .search(
                        [
                            ("default_code", "=", update_data["default_code"]),
                            ("id", "!=", product_id),
                        ]
                    )
                )
                if existing_code:
                    return {
                        "code": 400,
                        "msg": f"El código interno '{update_data['default_code']}' ya existe en otro producto",
                    }

            # Actualizar el producto si hay datos para actualizar
            if update_data:
                product.sudo().write(update_data)

            # Obtener almacenes permitidos para el usuario actual
            user = request.env.user
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # OBTENER INFORMACIÓN COMPLETA DEL PRODUCTO (como en la imagen)
            # Buscar quants considerando TODOS los almacenes permitidos
            quants = (
                request.env["stock.quant"]
                .sudo()
                .search(
                    [
                        ("product_id", "=", product.id),
                        ("quantity", ">", 0),
                        ("location_id.usage", "=", "internal"),
                        ("location_id.warehouse_id", "in", allowed_warehouses.ids),
                    ]
                )
            )

            ubicaciones = []
            for quant in quants:
                # Verificar que el almacén esté en los permitidos
                warehouse = (
                    request.env["stock.warehouse"]
                    .sudo()
                    .search(
                        [
                            ("id", "=", quant.location_id.warehouse_id.id),
                            ("id", "in", allowed_warehouses.ids),
                        ],
                        limit=1,
                    )
                )

                if not warehouse:
                    continue  # Saltar si no pertenece a un almacén del usuario

                if quant.inventory_quantity_auto_apply > 0:
                    ubicaciones.append(
                        {
                            "id_move": quant.id,
                            "id_almacen": warehouse.id,
                            "nombre_almacen": warehouse.name,
                            "id_ubicacion": quant.location_id.id,
                            "ubicacion": quant.location_id.complete_name or "",
                            "cantidad": quant.inventory_quantity_auto_apply or 0,
                            "reservado": quant.reserved_quantity or 0,
                            "cantidad_mano": quant.quantity - quant.reserved_quantity,
                            "codigo_barras": quant.location_id.barcode or "",
                            "lote": quant.lot_id.name if quant.lot_id else "",
                            "lote_id": quant.lot_id.id if quant.lot_id else 0,
                            "fecha_eliminacion": quant.removal_date or "",
                            "fecha_entrada": quant.in_date or "",
                        }
                    )

            # Obtener códigos de barras de paquetes
            paquetes = product.packaging_ids.mapped("barcode")

            # Construir respuesta completa como en la imagen
            response_data = {
                "code": 200,
                "type": "product",
                "result": {
                    "id": product.id,
                    "nombre": product.display_name,
                    "precio": product.list_price or 0.0,
                    "cantidad_disponible": product.qty_available,
                    "previsto": product.virtual_available,
                    "referencia": product.default_code,
                    "peso": product.weight or 0.0,
                    "volumen": product.volume or 0.0,
                    "codigo_barras": product.barcode or "",
                    "codigos_barras_paquetes": paquetes,
                    "imagen": product.image_128
                    and f"/web/image/product.product/{product.id}/image_128"
                    or "",
                    "categoria": product.categ_id.name if product.categ_id else "",
                    "ubicaciones": ubicaciones,
                },
            }

            # Si se proporcionaron campos para actualizar, agregar información de actualización
            if update_data:
                response_data["message"] = "Producto actualizado exitosamente"
                response_data["updated_fields"] = list(update_data.keys())
            else:
                response_data["message"] = "No se proporcionaron campos para actualizar el producto"

            return response_data

        except Exception as e:
            return {
                "code": 400,
                "msg": f"Error interno del servidor: {str(e)}",
                "error": str(e),
            }

    ## GET Información de un producto específico
    @http.route("/api/get_product", auth="user", type="json", methods=["GET", "POST"])
    def get_product(self, product_id):
        """
        Obtiene información de un producto específico
        """
        try:
            product_id = int(product_id) if product_id else None

            if not product_id:
                return {"success": False, "error": "El campo 'product_id' es requerido"}

            product = request.env["product.product"].sudo().browse(product_id)

            if not product.exists():
                return {
                    "success": False,
                    "error": f"No se encontró el producto con ID: {product_id}",
                }

            return {
                "code": 200,
                "msg": "Producto encontrado",
                "result": {
                    "id": product.id,
                    "name": product.name,
                    "default_code": product.default_code,
                    "barcode": product.barcode,
                    "categ_id": product.categ_id.id if product.categ_id else None,
                    "category_name": (product.categ_id.name if product.categ_id else None),
                    "list_price": product.list_price,
                    "standard_price": product.standard_price,
                    "weight": product.weight,
                    "volume": product.volume,
                    "description": product.description,
                    "active": product.active,
                    "tracking": product.tracking,
                    "type": product.type,
                    "qty_available": product.qty_available,
                    "virtual_available": product.virtual_available,
                },
            }

        except Exception as e:
            return {"code": 400, "msg": f"Error interno del servidor: {str(e)}"}

    ## GET Todas las categorías de productos
    @http.route("/api/get_product_categories", auth="user", type="json", methods=["GET", "POST"])
    def get_product_categories(self):
        """
        Obtiene todas las categorías de productos
        """
        try:
            categories = request.env["product.category"].sudo().search([])

            if not categories:
                return {"code": 404, "msg": "No se encontraron categorías de productos"}

            result = []
            for category in categories:
                result.append(
                    {
                        "id": category.id,
                        "name": category.name,
                        "parent_id": (category.parent_id.id if category.parent_id else None),
                        "complete_name": category.complete_name,
                    }
                )

            return {"code": 200, "result": result}

        except Exception as e:
            return {"code": 400, "msg": f"Error interno del servidor: {str(e)}"}

    ## POST Update información de una ubicación
    @http.route("/api/update_location", auth="user", type="json", methods=["POST"])
    def post_update_location(self, **auth):
        """
        Actualiza información de una ubicación en el WMS y devuelve información completa

        Parámetros esperados:
        - location_id: ID de la ubicación a actualizar
        - name: Nombre de la ubicación (opcional)
        - barcode: Código de barras (opcional)
        - usage: Uso de la ubicación (opcional, valores posibles: 'internal', 'transit', 'customer', 'supplier', 'inventory')
        - is_a_dock: Indica si es un muelle (opcional, booleano)
        - is_full: Indica si el muelle está lleno (opcional, booleano)
        """

        try:
            # Obtener datos del request
            location_id = auth.get("location_id")
            name = auth.get("name")
            barcode = auth.get("barcode")
            usage = auth.get("usage")
            is_a_dock = auth.get("is_a_dock")
            is_full = auth.get("is_full")

            # Validar que se proporcione el ID de la ubicación
            if not location_id:
                return {"code": 400, "msg": "El campo 'location_id' es requerido"}

            # Buscar la ubicación
            location = request.env["stock.location"].sudo().browse(location_id)

            if not location.exists():
                return {
                    "code": 404,
                    "msg": f"No se encontró la ubicación con ID: {location_id}",
                }

            # Preparar datos para actualizar
            update_data = {}

            # Agregar campos solo si vienen en el request y no están vacíos
            if name is not None and str(name).strip():
                update_data["name"] = name.strip()
            if barcode is not None and str(barcode).strip():
                update_data["barcode"] = barcode.strip()
            if usage is not None and str(usage).strip():
                update_data["usage"] = usage.strip()
            if is_a_dock is not None and is_a_dock != "":
                update_data["is_a_dock"] = is_a_dock
            if is_full is not None and is_full != "":
                update_data["is_full"] = is_full

            # Validaciones específicas
            if update_data.get("barcode"):
                # Verificar que el código de barras no esté duplicado
                existing_barcode = (
                    request.env["stock.location"]
                    .sudo()
                    .search(
                        [
                            ("barcode", "=", update_data["barcode"]),
                            ("id", "!=", location_id),
                        ]
                    )
                )
                if existing_barcode:
                    return {
                        "code": 400,
                        "msg": f"El código de barras '{update_data['barcode']}' ya existe en otra ubicación",
                    }

            # Actualizar la ubicación si hay datos para actualizar
            if update_data:
                location.sudo().write(update_data)

            # OBTENER INFORMACIÓN COMPLETA DE LA UBICACIÓN
            # Buscar todos los quants (stock) en esta ubicación
            quants = (
                request.env["stock.quant"]
                .sudo()
                .search([("location_id", "=", location.id), ("quantity", ">", 0)])
            )

            productos_dict = {}
            for quant in quants:
                prod = quant.product_id
                if prod.id not in productos_dict:
                    productos_dict[prod.id] = {
                        "id": prod.id,
                        "producto": prod.display_name,
                        "cantidad": 0.0,
                        "codigo_barras": prod.barcode or "",
                        "lote": quant.lot_id.name if quant.lot_id else "",
                        "lote_id": quant.lot_id.id if quant.lot_id else 0,
                        "id_almacen": (location.warehouse_id.id if location.warehouse_id else 0),
                        "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                    }
                productos_dict[prod.id]["cantidad"] += quant.available_quantity

            productos = list(productos_dict.values())

            # Construir respuesta completa
            response_data = {
                "code": 200,
                "type": "ubicacion",
                "result": {
                    "id": location.id,
                    "id_almacen": (location.warehouse_id.id if location.warehouse_id else 0),
                    "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                    "nombre": location.name,
                    "ubicacion_padre": (location.location_id.name if location.location_id else ""),
                    "tipo_ubicacion": location.usage,
                    "codigo_barras": location.barcode or "",
                    "productos": productos,
                },
            }

            # Si se proporcionaron campos para actualizar, agregar información de actualización
            if update_data:
                response_data["message"] = "Ubicación actualizada exitosamente"
                response_data["updated_fields"] = list(update_data.keys())
            else:
                response_data["message"] = "Información de la ubicación obtenida exitosamente"

            return response_data

        except Exception as e:
            return {
                "code": 400,
                "msg": f"Error interno del servidor: {str(e)}",
                "error": str(e),
            }

    ## POST Obtener imagen del producto
    @http.route("/api/send_image_product", auth="user", type="http", methods=["POST"], csrf=False)
    def send_image_product(self, **post):
        """
        Endpoint para crear/actualizar imagen de un producto
        """
        try:
            user = request.env.user
            if not user:
                return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

            product_id = post.get("product_id")
            image_file = request.httprequest.files.get("image_data")

            # Validar ID de producto
            if not product_id:
                return request.make_json_response({"code": 400, "msg": "ID de producto no válido"})

            # Validar archivo de imagen
            if not image_file:
                return request.make_json_response(
                    {"code": 400, "msg": "No se recibió ningún archivo de imagen"}
                )

            # Convertir ID a entero si viene como string
            try:
                product_id = int(product_id)
            except (ValueError, TypeError):
                return request.make_json_response({"code": 400, "msg": "ID de producto debe ser un número"})

            # Buscar el producto por ID
            product = request.env["product.product"].sudo().search([("id", "=", product_id)], limit=1)

            if not product:
                return request.make_json_response({"code": 404, "msg": "Producto no encontrado"})

            # Validar tipo de archivo
            allowed_extensions = ["jpg", "jpeg", "png", "gif", "bmp", "webp"]
            file_extension = image_file.filename.lower().split(".")[-1] if image_file.filename else ""
            if file_extension not in allowed_extensions:
                return request.make_json_response(
                    {
                        "code": 400,
                        "msg": f"Formato de imagen no permitido. Formatos válidos: {', '.join(allowed_extensions)}",
                    }
                )

            # Validar tamaño del archivo (máximo 5MB)
            max_size = 5 * 1024 * 1024
            image_file.seek(0, 2)
            file_size = image_file.tell()
            image_file.seek(0)

            if file_size > max_size:
                return request.make_json_response(
                    {"code": 400, "msg": "El archivo es demasiado grande. Tamaño máximo: 5MB"}
                )

            # Leer el contenido del archivo y codificarlo a base64
            image_data_bytes = image_file.read()
            image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

            # Información de la imagen para la respuesta
            base_url = request.httprequest.host_url.rstrip("/")
            image_info = {
                "filename": image_file.filename,
                "image_size": len(image_data_bytes),
                "image_url": f"{base_url}/api/view_imagen_product/{product_id}",
                "json_url": f"{base_url}/api/get_imagen_product/{product_id}",
            }

            # Actualizar el producto con la imagen
            # Odoo usa image_1920 para la imagen principal de mayor resolución
            product.sudo().write({"image_1920": image_data_base64})

            # Preparar respuesta
            response_data = {
                "code": 200,
                "result": "Imagen del producto guardada correctamente",
                "product_id": product_id,
                "product_name": product.name,
                "product_code": product.default_code or None,
                "image_processed": True,
            }

            response_data.update(image_info)

            return request.make_json_response(response_data)

        except Exception as e:
            return request.make_json_response({"code": 500, "msg": "Error interno del servidor"})

    ## GET Visualizar imagen del producto
    @http.route(
        "/api/view_imagen_product/<int:product_id>",
        auth="user",
        type="http",
        methods=["GET"],
        csrf=False,
    )
    def view_imagen_product(self, product_id, **kw):
        """
        Endpoint para visualizar la imagen de un producto
        """
        try:
            # Buscar el producto por ID
            product = request.env["product.product"].sudo().search([("id", "=", product_id)], limit=1)

            if not product:
                return request.make_response(
                    "Producto no encontrado",
                    status=404,
                    headers=[("Content-Type", "text/plain")],
                )

            # Verificar si tiene imagen (usar image_1920 que es la de mayor resolución)
            if not product.image_1920:
                return request.make_response(
                    "No hay imagen disponible para este producto",
                    status=404,
                    headers=[("Content-Type", "text/plain")],
                )

            # Decodificar la imagen de base64
            try:
                image_data = base64.b64decode(product.image_1920)
            except Exception as e:
                return request.make_response(
                    "Error al procesar la imagen",
                    status=500,
                    headers=[("Content-Type", "text/plain")],
                )

            # Detectar el tipo de contenido de la imagen
            content_type = "image/jpeg"  # Por defecto

            # Detectar tipo de imagen por los magic bytes
            if image_data.startswith(b"\x89PNG"):
                content_type = "image/png"
            elif image_data.startswith(b"\xff\xd8\xff"):
                content_type = "image/jpeg"
            elif image_data.startswith(b"GIF87a") or image_data.startswith(b"GIF89a"):
                content_type = "image/gif"
            elif image_data.startswith(b"RIFF") and b"WEBP" in image_data[:12]:
                content_type = "image/webp"
            elif image_data.startswith(b"BM"):
                content_type = "image/bmp"

            # Crear la respuesta con la imagen
            response = request.make_response(
                image_data,
                headers=[
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(image_data))),
                    ("Cache-Control", "public, max-age=3600"),
                    ("Content-Disposition", f"inline; filename=product_{product_id}.jpg"),
                ],
            )

            return response

        except Exception as e:
            return request.make_response(
                "Error interno del servidor", status=500, headers=[("Content-Type", "text/plain")]
            )

    ## GET Obtener imagen del producto en JSON
    @http.route(
        "/api/get_imagen_product/<int:product_id>",
        auth="user",
        type="json",
        methods=["GET"],
        csrf=False,
    )
    def get_imagen_product_json(self, product_id, **kw):
        """
        Endpoint que devuelve la imagen del producto en formato JSON con base64
        Incluye también información adicional del producto
        """
        try:
            # Buscar el producto por ID
            product = request.env["product.product"].sudo().search([("id", "=", product_id)], limit=1)

            if not product:
                return {"code": 404, "msg": "Producto no encontrado"}

            # Verificar si tiene imagen
            if not product.image_1920:
                return {"code": 404, "msg": ""}

            base_url = request.httprequest.host_url.rstrip("/")

            return {"code": 200, "result": {"url": f"{base_url}/api/view_imagen_product/{product_id}"}}

            # # Detectar tipo de imagen
            # image_data = base64.b64decode(product.image_1920)
            # content_type = "image/jpeg"  # Por defecto

            # if image_data.startswith(b"\x89PNG"):
            #     content_type = "image/png"
            # elif image_data.startswith(b"\xff\xd8\xff"):
            #     content_type = "image/jpeg"
            # elif image_data.startswith(b"GIF87a") or image_data.startswith(b"GIF89a"):
            #     content_type = "image/gif"
            # elif image_data.startswith(b"RIFF") and b"WEBP" in image_data[:12]:
            #     content_type = "image/webp"
            # elif image_data.startswith(b"BM"):
            #     content_type = "image/bmp"

            # return {
            #     "code": 200,
            #     "result": {
            #         "product_id": product_id,
            #         "image_base64": product.image_1920,
            #         "content_type": content_type,
            #         "size": len(image_data),
            #         "product_name": product.name,
            #         "product_code": product.default_code or None,
            #         "barcode": product.barcode or None,
            #         "list_price": product.list_price,
            #         "standard_price": product.standard_price,
            #         "type": product.type,
            #         "categ_id": product.categ_id.name if product.categ_id else None,
            #         "active": product.active,
            #     },
            # }

        except Exception as e:
            return {"code": 500, "msg": f"Error interno del servidor {str(e)}"}

    @http.route(
        "/api/delete_imagen_product/<int:product_id>",
        auth="user",
        type="json",
        methods=["DELETE"],
        csrf=False,
    )
    def delete_imagen_product(self, product_id, **kw):
        """
        Endpoint para eliminar la imagen de un producto
        """
        try:
            # Buscar el producto por ID
            product = request.env["product.product"].sudo().search([("id", "=", product_id)], limit=1)

            if not product:
                return {"code": 404, "msg": "Producto no encontrado"}

            # Verificar si tiene imagen
            if not product.image_1920:
                return {"code": 404, "msg": "No hay imagen para eliminar"}

            # Eliminar la imagen (elimina todas las variantes de imagen)
            product.sudo().write(
                {
                    "image_1920": False,
                }
            )

            return {
                "code": 200,
                "result": "Imagen del producto eliminada correctamente",
                "product_id": product_id,
            }

        except Exception as e:
            return {"code": 500, "msg": "Error interno del servidor"}

    @http.route(
        "/api/update_imagen_product/<int:product_id>",
        auth="user",
        type="http",
        methods=["PUT"],
        csrf=False,
    )
    def update_imagen_product(self, product_id, **post):
        """
        Endpoint para actualizar solo la imagen de un producto existente
        """
        try:
            user = request.env.user
            if not user:
                return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

            image_file = request.httprequest.files.get("image_data")

            # Buscar el producto por ID
            product = request.env["product.product"].sudo().search([("id", "=", product_id)], limit=1)

            if not product:
                return request.make_json_response({"code": 404, "msg": "Producto no encontrado"})

            # Validar archivo de imagen si se envía
            if not image_file:
                return request.make_json_response(
                    {"code": 400, "msg": "No se recibió ningún archivo de imagen"}
                )

            # Validar tipo de archivo
            allowed_extensions = ["jpg", "jpeg", "png", "gif", "bmp", "webp"]
            file_extension = image_file.filename.lower().split(".")[-1] if image_file.filename else ""
            if file_extension not in allowed_extensions:
                return request.make_json_response({"code": 400, "msg": "Formato de imagen no permitido"})

            # Validar tamaño del archivo (máximo 5MB)
            max_size = 5 * 1024 * 1024
            image_file.seek(0, 2)
            file_size = image_file.tell()
            image_file.seek(0)

            if file_size > max_size:
                return request.make_json_response(
                    {"code": 400, "msg": "El archivo es demasiado grande. Tamaño máximo: 5MB"}
                )

            # Leer el contenido del archivo y codificarlo a base64
            image_data_bytes = image_file.read()
            image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

            # Actualizar el producto
            product.sudo().write({"image_1920": image_data_base64})

            return request.make_json_response(
                {
                    "code": 200,
                    "result": "Imagen del producto actualizada correctamente",
                    "product_id": product_id,
                    "product_name": product.name,
                    "image_size": len(image_data_bytes),
                }
            )

        except Exception as e:
            return request.make_json_response({"code": 500, "msg": f"Error interno del servidor: {str(e)}"})


def obtener_almacenes_usuario(user):

    user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

    if not user_wms:
        return {
            "code": 401,
            "msg": "El usuario no tiene permisos o no esta registrado en el módulo de configuraciones en el WMS",
        }

    allowed_warehouses = user_wms.allowed_warehouse_ids

    if not allowed_warehouses:
        return {"code": 400, "msg": "El usuario no tiene acceso a ningún almacén"}

    return allowed_warehouses
