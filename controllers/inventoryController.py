import json
from datetime import date, datetime, timedelta

import pytz
from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from .utils import get_barcodes, get_packagings


class InventoryController(http.Controller):
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
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/inventory/all_orders", type="json", auth="user", methods=["GET"], csrf=False)
    def get_all_orders(self, **kwargs):
        try:
            version_app = kwargs.get("version_app") or request.params.get("version_app")

            # 1. Llama al método que obtiene la última versión
            response_version = self.get_last_version()

            # 2. Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # 3. Compara las versiones
            update_required = False
            if version_app:
                try:
                    app_parts = list(map(int, version_app.split(".")))
                    latest_parts = list(map(int, latest_version_str.split(".")))
                    if app_parts < latest_parts:
                        update_required = True
                except (ValueError, TypeError):
                    update_required = True  # Requiere actualización si el formato es incorrecto
            else:
                update_required = True  # Requiere actualización si no se envía versión

            user = request.env.user

            if not user:
                return {"code": 400, "update_version": update_required, "msg": "Usuario no encontrado"}

            all_orders = []

            # Obtener los almacenes permitidos para el usuario
            allowed_warehouses = obtener_almacenes_usuario_wms(user)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

            for warehouse in allowed_warehouses:
                ordenes_pendentes = (
                    request.env["bexwms_counted.order"]
                    .sudo()
                    .search(
                        [
                            ("state_count", "in", ["in_progress"]),
                            ("warehouse_id", "=", warehouse.id),
                            ("user_id", "=", user.id),
                        ],
                        order="create_date desc",
                    )
                )

                for order in ordenes_pendentes:
                    filter_type, enable_all_locations, enable_all_products = get_filter_type_and_permissions(
                        order
                    )

                    orders_data = {
                        "id": order.id,
                        "name": order.name,
                        "state": order.state_count,
                        "warehouse_id": order.warehouse_id.id,
                        "warehouse_name": order.warehouse_id.name,
                        "responsable_id": order.user_id.id,
                        "responsable_name": order.user_id.name,
                        "create_date": (
                            order.create_date.strftime("%Y-%m-%d %H:%M:%S") if order.create_date else ""
                        ),
                        "date_count": order.date_count.strftime("%Y-%m-%d") if order.date_count else "",
                        "mostrar_cantidad": order.show_quantity_operation,
                        "observation_general": order.observation_general or "",
                        "count_type": order.count_type,
                        "number_count": order.number_count,
                        "numero_lineas": len(order.line_ids),
                        "numero_items_contados": len(order.line_ids.filtered(lambda l: l.is_done_item)),
                        "filter_type": filter_type,
                        "enable_all_locations": enable_all_locations,
                        "enable_all_products": enable_all_products,
                        "allowed_categories": [
                            {"id": cat.id, "name": cat.name, "orden_conteo_id": order.id}
                            for cat in order.category_ids
                        ],
                        "allowed_locations": [
                            {
                                "id": loc.id,
                                "name": loc.display_name,
                                "orden_conteo_id": order.id,
                                "barcode": loc.barcode or "",
                            }
                            for loc in order.location_ids
                        ],
                        "allowed_products": [
                            {"id": prod.id, "name": prod.display_name, "orden_conteo_id": order.id}
                            for prod in order.product_ids
                        ],
                        "counted_lines": [],
                        "counted_lines_done": [],
                    }

                    # Obtener las líneas de conteo
                    for line in order.line_ids:
                        product = line.product_id

                        array_barcodes = (
                            [
                                {
                                    "barcode": barcode.name or "",
                                    "id_move": line.id,
                                    "id_product": product.id,
                                    "batch_id": order.id,
                                    "cantidad": 0,
                                    "barcode_type": "",
                                }
                                for barcode in product.barcode_ids
                                if barcode.name
                            ]
                            if hasattr(product, "barcode_ids")
                            else []
                        )

                        line_data = {
                            "id": line.id,
                            "is_original": line.is_original,
                            "id_move": line.id,
                            "order_id": order.id,
                            "product_id": product.id,
                            "product_name": product.display_name,
                            "product_code": product.default_code or "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "use_expiration_date": product.use_expiration_date or False,
                            "expiration_time": product.expiration_time,
                            "other_barcodes": get_barcodes(product, line.id, order.id),
                            "product_packing": [
                                {
                                    "barcode": p.barcode,
                                    "cantidad": p.qty,
                                    "id_product": p.product_id.id,
                                    "id_move": line.id,
                                    "batch_id": order.id,
                                }
                                for p in getattr(product, "packaging_ids", [])
                                if p.barcode and p.barcode.strip()
                            ],
                            "location_id": line.location_id.id,
                            "location_name": line.location_id.display_name,
                            "location_barcode": line.location_id.barcode or "",
                            "quantity_inventory": line.quantity_inventory,
                            "quantity_counted": line.quantity_counted,
                            "difference_qty": line.difference_qty,
                            "uom": product.uom_id.name if product.uom_id else "UND",
                            "weight": product.weight or 0,
                            "is_done_item": line.is_done_item,
                            "date_transaction": (
                                line.date_transaction.strftime("%Y-%m-%d %H:%M:%S")
                                if line.date_transaction
                                else ""
                            ),
                            "observation": line.new_observation or "",
                            "time": line.time or 0,
                            "user_operator_id": line.user_operator_id.id if line.user_operator_id else 0,
                            "user_operator_name": line.user_operator_id.name if line.user_operator_id else "",
                            "category_id": line.product_categ_id.id if line.product_categ_id else 0,
                            "category_name": line.product_categ_id.name if line.product_categ_id else "",
                        }

                        # Información del lote si existe
                        if line.lot_id:
                            line_data.update(
                                {
                                    "lot_id": line.lot_id.id,
                                    "lot_name": line.lot_id.name,
                                    "fecha_vencimiento": (
                                        line.lot_id.expiration_date.strftime("%Y-%m-%d")
                                        if line.lot_id.expiration_date
                                        else ""
                                    ),
                                }
                            )
                        else:
                            line_data.update(
                                {
                                    "lot_id": 0,
                                    "lot_name": "",
                                    "fecha_vencimiento": "",
                                }
                            )

                        # Separar líneas según si están contadas o no
                        if line.is_done_item:
                            orders_data["counted_lines_done"].append(line_data)
                        else:
                            orders_data["counted_lines"].append(line_data)

                    all_orders.append(orders_data)

            return {
                "code": 200,
                "update_version": update_required,
                "msg": "Órdenes de inventario obtenidas correctamente",
                "data": all_orders,
                "user_id": user.id,
                "allowed_warehouses": [{"id": wh.id, "name": wh.name} for wh in allowed_warehouses],
            }

        except Exception as e:
            return {
                "code": 500,
                "update_version": update_required,
                "msg": f"Error interno del servidor: {str(e)}",
                "data": [],
            }

    ## obtener orden por id
    @http.route("/api/inventory/order/<int:order_id>", type="json", auth="user", methods=["GET"], csrf=False)
    def get_order_by_id(self, order_id):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            order = (
                request.env["bexwms_counted.order"]
                .sudo()
                .search([("id", "=", order_id), ("user_id", "=", user.id)], limit=1)
            )

            if not order:
                return {"code": 404, "msg": "Orden de inventario no encontrada"}

            filter_type, enable_all_locations, enable_all_products = get_filter_type_and_permissions(order)

            orders_data = {
                "id": order.id,
                "name": order.name,
                "state": order.state_count,
                "warehouse_id": order.warehouse_id.id,
                "warehouse_name": order.warehouse_id.name,
                "responsable_id": order.user_id.id,
                "responsable_name": order.user_id.name,
                "create_date": order.create_date.strftime("%Y-%m-%d %H:%M:%S") if order.create_date else "",
                "date_count": order.date_count.strftime("%Y-%m-%d") if order.date_count else "",
                "mostrar_cantidad": order.show_quantity_operation,
                "count_type": order.count_type,
                "number_count": order.number_count,
                "numero_lineas": len(order.line_ids),
                "numero_items_contados": len(order.line_ids.filtered(lambda l: l.is_done_item)),
                "filter_type": filter_type,
                "enable_all_locations": enable_all_locations,
                "enable_all_products": enable_all_products,
                "allowed_categories": [
                    {"id": cat.id, "name": cat.name, "orden_conteo_id": order.id}
                    for cat in order.category_ids
                ],
                "allowed_locations": [
                    {"id": loc.id, "name": loc.display_name, "orden_conteo_id": order.id}
                    for loc in order.location_ids
                ],
                "allowed_products": [
                    {"id": prod.id, "name": prod.display_name, "orden_conteo_id": order.id}
                    for prod in order.product_ids
                ],
                "counted_lines": [],
                "counted_lines_done": [],
            }

            # Obtener las líneas de conteo
            for line in order.line_ids:
                product = line.product_id

                line_data = {
                    "id": line.id,
                    "order_id": order.id,
                    "product_id": product.id,
                    "product_name": product.display_name,
                    "product_code": product.default_code or "",
                    "product_barcode": product.barcode or "",
                    "product_tracking": product.tracking or "",
                    "other_barcodes": get_barcodes(product, line.id, order.id),
                    "product_packing": [
                        {
                            "barcode": p.barcode,
                            "cantidad": p.qty,
                            "id_product": p.product_id.id,
                            "id_line": line.id,
                            "order_id": order.id,
                        }
                        for p in getattr(product, "packaging_ids", [])
                    ],
                    "location_id": line.location_id.id,
                    "location_name": line.location_id.display_name,
                    "location_barcode": line.location_id.barcode or "",
                    "quantity_inventory": line.quantity_inventory,
                    "quantity_counted": line.quantity_counted,
                    "difference_qty": line.difference_qty,
                    "uom": product.uom_id.name if product.uom_id else "UND",
                    "weight": product.weight or 0,
                    "is_done_item": line.is_done_item,
                    "date_transaction": (
                        line.date_transaction.strftime("%Y-%m-%d %H:%M:%S") if line.date_transaction else ""
                    ),
                    "observation": line.new_observation or "",
                    "time": line.time or 0,
                    "user_operator_id": line.user_operator_id.id if line.user_operator_id else 0,
                    "user_operator_name": line.user_operator_id.name if line.user_operator_id else "",
                    "category_id": line.product_categ_id.id if line.product_categ_id else 0,
                    "category_name": line.product_categ_id.name if line.product_categ_id else "",
                }

                # Información del lote si existe
                if line.lot_id:
                    line_data.update(
                        {
                            "lot_id": line.lot_id.id,
                            "lot_name": line.lot_id.name,
                            "fecha_vencimiento": (
                                line.lot_id.expiration_date.strftime("%Y-%m-%d")
                                if line.lot_id.expiration_date
                                else ""
                            ),
                        }
                    )
                else:
                    line_data.update(
                        {
                            "lot_id": 0,
                            "lot_name": "",
                            "fecha_vencimiento": "",
                        }
                    )

                # Separar líneas según si están contadas o no
                if line.is_done_item:
                    orders_data["counted_lines_done"].append(line_data)
                else:
                    orders_data["counted_lines"].append(line_data)

            return {
                "code": 200,
                "msg": "Orden de inventario obtenida correctamente",
                "data": orders_data,
            }
        except Exception as e:
            return {"code": 500, "msg": f"Error interno del servidor: {str(e)}", "data": {}}

    ## Envio de datos de inventario
    @http.route("/api/inventory/send_inventory", type="json", auth="user", methods=["POST"], csrf=False)
    def send_inventory(self, **kwargs):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            order_id = kwargs.get("order_id")
            list_items = kwargs.get("list_items", [])

            order = request.env["bexwms_counted.order"].sudo().search([("id", "=", order_id)], limit=1)

            if not order:
                return {"code": 404, "msg": "Orden de inventario no encontrada"}

            # Validar que el estado de la orden sea 'in_progress'
            if order.state_count != "in_progress":
                return {"code": 400, "msg": "La orden de inventario no está en estado 'in_progress'"}

            array_result = []

            # Procesar las líneas enviadas
            for item in list_items:
                line_id = item.get("line_id")
                quantity_counted = item.get("quantity_counted", 0)
                observation = item.get("observation", "")
                user_operator_id = item.get("id_operario", user.id)
                fecha_transaccion = item.get("fecha_transaccion", "")
                time = item.get("time_line", 0)
                location_id = item.get("location_id")
                product_id = item.get("product_id")
                lote_id = item.get("lote_id", 0)
                option = ""

                # ====== VALIDACIONES AGREGADAS ======

                # 1. Validar product_id
                if product_id:
                    product = request.env["product.product"].sudo().browse(product_id)
                    if not product.exists():
                        return {"code": 400, "msg": f"El producto con ID {product_id} no existe"}
                else:
                    return {"code": 400, "msg": "product_id es requerido"}

                # 2. Validar location_id
                if location_id:
                    location = request.env["stock.location"].sudo().browse(location_id)
                    if not location.exists():
                        return {"code": 400, "msg": f"La ubicación con ID {location_id} no existe"}
                else:
                    return {"code": 400, "msg": "location_id es requerido"}

                # 3. Validar lote_id (opcional)
                lot_record = None
                if lote_id and lote_id != 0:
                    lot_record = request.env["stock.lot"].sudo().browse(lote_id)
                    if not lot_record.exists():
                        return {"code": 400, "msg": f"El lote con ID {lote_id} no existe"}

                # 4. Validar user_operator_id
                if user_operator_id:
                    operator = request.env["res.users"].sudo().browse(user_operator_id)
                    if not operator.exists():
                        return {"code": 400, "msg": f"El operario con ID {user_operator_id} no existe"}

                # 5. Convertir line_id a entero si viene como string
                try:
                    line_id = int(line_id) if line_id else None
                except (ValueError, TypeError):
                    return {"code": 400, "msg": f"line_id debe ser un número válido: {line_id}"}

                # ====== FIN VALIDACIONES ======

                # Usar savepoint para manejar errores específicos
                with request.env.cr.savepoint():
                    # Buscar la línea de conteo
                    line = None
                    if line_id:
                        line = (
                            request.env["bexwms_counted.order.line"]
                            .sudo()
                            .search([("id", "=", line_id)], limit=1)
                        )

                    if not line:
                        # Si la línea no se encuentra, crear una nueva línea de conteo
                        line_values = {
                            "counted_id": order.id,
                            "product_id": product_id,
                            "location_id": location_id,
                            "quantity_inventory": item.get("quantity_inventory", 0),
                            "quantity_counted": quantity_counted,
                            "new_observation": observation,
                            "user_operator_id": user_operator_id,
                            "is_done_item": True,
                            "date_transaction": (
                                procesar_fecha_naive(fecha_transaccion, user.tz)
                                if fecha_transaccion
                                else datetime.now().replace(tzinfo=None)
                            ),
                            "time": time,
                            "is_original": False,
                        }

                        # Agregar lot_id solo si existe
                        if lot_record:
                            line_values["lot_id"] = lote_id

                        line = request.env["bexwms_counted.order.line"].sudo().create(line_values)
                        option = "create"
                    else:
                        # Actualizar la línea de conteo existente
                        update_values = {
                            "quantity_counted": quantity_counted,
                            "new_observation": observation,
                            "user_operator_id": user_operator_id,
                            "is_done_item": True,
                            "date_transaction": (
                                procesar_fecha_naive(fecha_transaccion, user.tz)
                                if fecha_transaccion
                                else datetime.now().replace(tzinfo=None)
                            ),
                            "time": time,
                        }

                        # Actualizar lot_id solo si existe
                        if lot_record:
                            update_values["lot_id"] = lote_id

                        line.write(update_values)
                        option = "update"

                    search_domain = [
                        ("order_id", "=", order.id),
                        ("user_id", "=", user.id),  # ✅ AGREGADO
                        ("number_count", "=", order.number_count),  # ✅ AGREGADO
                        ("warehouse_id", "=", order.warehouse_id.id),
                        ("location_id", "=", line.location_id.id),
                        ("product_id", "=", line.product_id.id),
                        ("quantity", "=", line.quantity_counted or 0),  # ✅ AGREGADO
                    ]

                    if lote_id:
                        search_domain.append(("lot_id", "=", lote_id))
                    else:
                        search_domain.append(("lot_id", "=", False))  # ✅ Manejo explícito de lotes vacíos

                    existing_log = (
                        request.env["bexwms_counted.bexwms_counted"].sudo().search(search_domain, limit=1)
                    )

                    if existing_log:
                        # Actualizar el log existente
                        existing_log.write(
                            {
                                "quantity": line.quantity_counted or 0,
                            }
                        )
                    else:
                        # Crear nuevo log solo si no existe
                        log = (
                            request.env["bexwms_counted.bexwms_counted"]
                            .sudo()
                            .create(
                                {
                                    "order_id": order.id,
                                    "user_id": user.id,
                                    "number_count": order.number_count,
                                    "warehouse_id": order.warehouse_id.id,
                                    "location_id": line.location_id.id,
                                    "product_id": line.product_id.id,
                                    "lot_id": line.lot_id.id if line.lot_id else False,
                                    "quantity": line.quantity_counted or 0,
                                }
                            )
                        )

                    # Agregar al resultado
                    array_result.append(
                        {
                            "line_id": line.id,
                            "product_id": line.product_id.id,
                            "lot_id": line.lot_id.id if line.lot_id else 0,
                            "lot_name": line.lot_id.name if line.lot_id else "",
                            "quantity_counted": quantity_counted,
                            "observation": observation,
                            "user_operator_id": user_operator_id,
                            "date_transaction": (
                                line.date_transaction.strftime("%Y-%m-%d %H:%M:%S")
                                if line.date_transaction
                                else ""
                            ),
                            "existing_log": existing_log.id if existing_log else False,
                            "option": option,
                        }
                    )

            return {
                "code": 200,
                "msg": "Inventario enviado correctamente",
                "result": array_result,
                "data": {
                    "order_id": order.id,
                    "numero_items_contados": len(order.line_ids.filtered(lambda l: l.is_done_item)),
                },
            }

        except AccessError as e:
            request.env.cr.rollback()
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as e:
            request.env.cr.rollback()
            return {"code": 500, "msg": f"Error interno del servidor: {str(e)}", "data": {}}

    ## Eliminar datos de la linea
    @http.route("/api/inventory/delete_line", type="json", auth="user", methods=["POST"], csrf=False)
    def delete_line(self, **kwargs):
        try:
            line_id = kwargs.get("line_id")
            if not line_id:
                return {"code": 400, "msg": "Falta el ID de la línea"}

            line = request.env["bexwms_counted.order.line"].sudo().browse(line_id)
            if not line:
                return {"code": 404, "msg": "Línea no encontrada"}

            array_result = []

            ## Limpiar los datos de la linea
            line.write(
                {
                    "quantity_counted": 0,
                    "new_observation": "",
                    "user_operator_id": False,
                    "is_done_item": False,
                    "date_transaction": False,
                    "time": False,
                }
            )

            array_result.append(
                {
                    "line_id": line.id,
                    "product_id": line.product_id.id,
                    "quantity_counted": 0,
                    "new_observation": "",
                    "user_operator_id": False,
                    "date_transaction": "",
                }
            )

            return {"code": 200, "msg": "Datos de la línea limpiados correctamente", "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as e:
            return {"code": 500, "msg": f"Error interno del servidor: {str(e)}", "data": {}}

    ## eliminar una linea definitivamente
    @http.route("/api/inventory/remove_line", type="json", auth="user", methods=["POST"], csrf=False)
    def remove_line(self, **kwargs):
        try:
            line_id = kwargs.get("line_id")
            if not line_id:
                return {"code": 400, "msg": "Falta el ID de la línea"}

            array_result = []

            line = request.env["bexwms_counted.order.line"].sudo().browse(line_id)

            if not line:
                return {"code": 404, "msg": "Línea no encontrada"}

            array_result.append(
                {
                    "line_id": line.id,
                    "product_id": line.product_id.id,
                    "quantity_counted": line.quantity_counted,
                    "new_observation": line.new_observation,
                    "user_operator_id": line.user_operator_id.id if line.user_operator_id else False,
                    "date_transaction": line.date_transaction,
                }
            )

            line.unlink()

            return {"code": 200, "msg": "Línea eliminada correctamente", "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as e:
            return {"code": 500, "msg": f"Error interno del servidor: {str(e)}", "data": {}}


def procesar_fecha_naive(fecha_transaccion, zona_horaria_cliente):
    if fecha_transaccion:
        # Convertir la fecha enviada a datetime y agregar la zona horaria del cliente
        tz_cliente = pytz.timezone(zona_horaria_cliente)
        fecha_local = tz_cliente.localize(datetime.strptime(fecha_transaccion, "%Y-%m-%d %H:%M:%S"))

        # Convertir la fecha a UTC
        fecha_utc = fecha_local.astimezone(pytz.utc)

        # Eliminar la información de la zona horaria (hacerla naive)
        fecha_naive = fecha_utc.replace(tzinfo=None)
        return fecha_naive
    else:
        # Usar la fecha actual del servidor como naive datetime
        return datetime.now().replace(tzinfo=None)


def obtener_almacenes_usuario_onpoint(user):
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


def obtener_almacenes_usuario_wms(user):
    """
    Obtiene los almacenes permitidos para el usuario en el módulo WMS.
    """

    user_wms = request.env["res.users"].sudo().search([("id", "=", user.id)], limit=1)

    if not user_wms:
        return {
            "code": 401,
            "msg": "El usuario no tiene permisos o no esta registrado en el módulo de configuraciones en el WMS",
        }

    allowed_warehouses = user_wms.allowed_warehouse_ids

    if not allowed_warehouses:
        return {"code": 400, "msg": "El usuario no tiene acceso a ningún almacén"}

    return allowed_warehouses


def get_filter_type_and_permissions(order):
    """
    Determina el tipo de filtro y los permisos basado en los campos del order
    """

    has_locations = bool(order.location_ids)
    has_categories = bool(order.category_ids)
    has_products = bool(order.product_ids)

    # Determinar tipo de filtro
    if has_locations and (has_categories or has_products):
        filter_type = "combined"
        enable_all_locations = False
        enable_all_products = False
    elif has_locations:
        filter_type = "location"
        enable_all_locations = False
        enable_all_products = True  # Puede escanear cualquier producto
    elif has_categories:
        filter_type = "category"
        enable_all_locations = True  # Puede escanear en cualquier ubicación
        enable_all_products = False
    elif has_products:
        filter_type = "product"
        enable_all_locations = True  # Puede escanear en cualquier ubicación
        enable_all_products = False
    else:
        # Sin filtros específicos - conteo general
        filter_type = "general"
        enable_all_locations = True
        enable_all_products = True

    return filter_type, enable_all_locations, enable_all_products
