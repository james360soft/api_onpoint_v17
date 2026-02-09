from calendar import c
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, timedelta
import pytz
from datetime import date
import base64
import logging
from .utils import get_barcodes, get_packagings



class TransaccionRecepcionController(http.Controller):

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

    # GET OBTENER LAS RECEPCIONES PENDIENTES
    @http.route("/api/recepciones", auth="user", type="json", methods=["GET"])
    def get_recepciones(self, **kwargs):
        try:
            version_app = kwargs.get("version_app")

            # 1. Llama al otro método DENTRO de esta misma clase
            response_version = self.get_last_version()

            # 2. Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # 3. Compara las versiones
            update_required = False
            if version_app:
                app_parts = list(map(int, version_app.split(".")))
                latest_parts = list(map(int, latest_version_str.split(".")))
                if app_parts < latest_parts:
                    update_required = True
            else:
                update_required = True

            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Usuario no encontrado",
                }

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_recepciones = []

            base_url = request.httprequest.host_url.rstrip("/")

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # Obtener la configuración actual
            # config = request.env["res.config.settings"].sudo().create({})
            # consigna_habilitada = config.group_stock_tracking_owner
            consigna_habilitada = user.has_group('stock.group_tracking_owner')

            # ✅ Obtener recepciones pendientes directamente de los almacenes permitidos
            for warehouse in allowed_warehouses:
                # Buscar todas las recepciones pendientes (no completadas ni canceladas) para este almacén
                recepciones_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "in", ["assigned", "confirmed"]),
                            ("picking_type_code", "=", "incoming"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("picking_type_id.sequence_code", "in", ["IN", "EE", "TRE"]),
                            # ("is_return_picking", "=", False),
                            # ("user_id", "in", [user.id, False]),  # Asignadas al usuario o sin asignar
                            (
                                "responsable_id",
                                "in",
                                [user.id, False],
                            ),  # Asignadas al usuario o sin asignar
                        ]
                    )
                )

                for picking in recepciones_pendientes:
                    # Verificar si hay movimientos pendientes
                    # En Odoo 17, move_lines se cambió a move_ids o move_ids_without_package
                    # movimientos_pendientes = picking.move_ids.filtered(lambda m: m.state in ["confirmed", "assigned"])
                    movimientos_pendientes = picking.move_ids

                    # Si no hay movimientos pendientes, omitir esta recepción
                    if not movimientos_pendientes:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = (
                        picking.picking_type_id.create_backorder
                        if hasattr(picking.picking_type_id, "create_backorder")
                        else False
                    )

                    # Obtener la orden de compra relacionada (si existe)
                    purchase_order = picking.purchase_id or (
                        picking.origin
                        and request.env["purchase.order"]
                        .sudo()
                        .search([("name", "=", picking.origin)], limit=1)
                    )

                    # Calcular peso total - cambiando product_qty por product_uom_qty
                    peso_total = sum(
                        move.product_id.weight * move.product_uom_qty
                        for move in movimientos_pendientes
                        if move.product_id.weight
                    )

                    # Calcular número de ítems (suma total de cantidades)
                    numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

                    manejo_temperatura = False

                    productos_con_temperatura = picking.move_ids.mapped("product_id").filtered(
                        lambda p: hasattr(p, "temperature_control") and p.temperature_control
                    )
                    if productos_con_temperatura:
                        manejo_temperatura = True

                    # Obtener el ID del propietario
                    owner_id = picking.owner_id.id if hasattr(picking, "owner_id") and picking.owner_id else 0

                    # Obtener el nombre del propietario (si existe el ID)
                    propietario_nombre = ""
                    if owner_id:
                        partner = request.env["res.partner"].sudo().browse(owner_id)
                        propietario_nombre = partner.name if partner else ""

                    recepcion_info = {
                        "id": picking.id,
                        "name": picking.name,  # Nombre de la recepción
                        "fecha_creacion": picking.create_date,  # Fecha con hora
                        "proveedor_id": picking.partner_id.id or 0,
                        "proveedor": picking.partner_id.name or "",
                        "location_dest_id": picking.location_dest_id.id or "",
                        "location_dest_name": picking.location_dest_id.display_name or "",
                        "purchase_order_id": purchase_order.id if purchase_order else 0,
                        "purchase_order_name": (
                            purchase_order.name if purchase_order else ""
                        ),  # Orden de compra
                        "numero_entrada": picking.name or "",  # Número de entrada
                        "peso_total": peso_total,  # Peso total
                        "numero_lineas": len(picking.move_ids),  # Número de líneas (productos)
                        "numero_items": numero_items,  # Número de ítems (cantidades)
                        "state": picking.state,
                        "create_backorder": create_backorder,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "responsable_id": (picking.responsable_id.id if picking.responsable_id else 0),
                        "responsable": (picking.responsable_id.name if picking.responsable_id else ""),
                        "picking_type": picking.picking_type_id.name,
                        "backorder_id": picking.backorder_id.id if picking.backorder_id else 0,
                        "backorder_name": (
                            picking.backorder_id.name if picking.backorder_id else ""
                        ),  # Nombre del backorder
                        # Verificar si los campos personalizados existen
                        "start_time_reception": picking.start_time_reception or "",
                        "end_time_reception": picking.end_time_reception or "",
                        "picking_type_code": picking.picking_type_code,
                        "show_check_availability": (
                            picking.show_check_availability
                            if hasattr(picking, "show_check_availability")
                            else False
                        ),
                        "maneja_temperatura": manejo_temperatura,
                        "temperatura": (picking.temperature if hasattr(picking, "temperature") else 0),
                        "manejo_propetario": consigna_habilitada,
                        "propetario": propietario_nombre,
                        "lineas_recepcion": [],
                        "lineas_recepcion_enviadas": [],
                    }

                    # ✅ Procesar solo las líneas pendientes
                    for move in movimientos_pendientes:
                        product = move.product_id
                        purchase_line = move.purchase_line_id

                        cantidad_faltante = move.product_uom_qty - sum(
                            l.quantity for l in move.move_line_ids if l.is_done_item
                        )

                        novedad_bloqueante = move.move_line_ids.filtered(
                            lambda ml: ml.new_observation
                            and ml.new_observation.strip()
                            and ml.new_observation.strip().lower() != "sin novedad"
                        )

                        maneja_temperatura = (
                            product.temperature_control if hasattr(product, "temperature_control") else False
                        )
                        temperatura = move.temperature if hasattr(move, "temperature") else 0
                        imagen = move.imagen if hasattr(move, "imagen") else ""

                        # Obtener cantidad ordenada
                        quantity_ordered = 0
                        if purchase_line and purchase_line.product_uom_qty:
                            quantity_ordered = purchase_line.product_uom_qty
                        else:
                            quantity_ordered = move.product_uom_qty

                        # En Odoo 17, quantity_done ya no existe, se usa quantity
                        quantity_done = move.quantity

                        # # ⚠️ Saltar líneas totalmente recepcionadas
                        # if quantity_done < quantity_ordered:

                        if not move.picked:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search(
                                    [("product_id", "=", product.id)],
                                    order="expiration_date asc",
                                    limit=1,
                                )
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": (
                                    product.expiration_time if hasattr(product, "expiration_time") else ""
                                ),
                                "use_expiration_date": product.use_expiration_date,
                                # "other_barcodes": array_barcodes,
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": array_packing,
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                                "maneja_temperatura": maneja_temperatura,
                                "temperatura": temperatura,
                                # "imagen": imagen,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        # necesito que se valide que si en move_line_ids no hay novedades diferentes a Sin novedad o diferente a vacio. Por ejemplo si la novedad es diferente a "Sin novedad" o está vacía, entonces se debe considerar como una línea pendiente.
                        elif cantidad_faltante > 0 and not novedad_bloqueante:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search(
                                    [("product_id", "=", product.id)],
                                    order="expiration_date asc",
                                    limit=1,
                                )
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": (
                                    product.expiration_time if hasattr(product, "expiration_time") else ""
                                ),
                                "use_expiration_date": product.use_expiration_date,
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": array_packing,
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                                "maneja_temperatura": maneja_temperatura,
                                "temperatura": temperatura,
                                # "imagen": imagen,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        # ✅ Agregar las líneas de move_line que tengan is_done_item en True
                        # Verificación para campos personalizados

                        move_lines_done = move.move_line_ids.filtered(lambda ml: ml.is_done_item)
                        for move_line in move_lines_done:
                            cantidad_faltante = move.product_uom_qty - move_line.quantity

                            date_transaccion = (
                                move_line.date_transaction if hasattr(move_line, "date_transaction") else None
                            )

                            # restarle 5 horas a la fecha de transaccion para ajustarla a la zona horaria

                            if date_transaccion:
                                date_transaccion = date_transaccion - timedelta(hours=5)

                            # Crear información de la línea enviada
                            linea_enviada_info = {
                                "id": move_line.id,
                                "id_move_line": move_line.id,
                                "id_move": move_line.id,
                                # "id_move": move.id,
                                "state": move_line.state,
                                "id_recepcion": picking.id,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "use_expiration_date": (
                                    product.use_expiration_date
                                    if hasattr(product, "use_expiration_date")
                                    else False
                                ),
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                "quantity_done": move_line.quantity,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
                                "location_dest_id": move_line.location_dest_id.id or 0,
                                "location_dest_name": move_line.location_dest_id.display_name or "",
                                "location_dest_barcode": move_line.location_dest_id.barcode or "",
                                "location_id": move_line.location_id.id or 0,
                                "location_name": move_line.location_id.display_name or "",
                                "location_barcode": move_line.location_id.barcode or "",
                                # Campos personalizados con manejo de fallback
                                "is_done_item": (
                                    move_line.is_done_item
                                    if hasattr(move_line, "is_done_item")
                                    else (move_line.quantity > 0)
                                ),
                                # "date_transaction": (
                                #     move_line.date_transaction
                                #     if hasattr(move_line, "date_transaction")
                                #     else ""
                                # ),
                                "date_transaction": date_transaccion,
                                "observation": (
                                    move_line.new_observation if hasattr(move_line, "new_observation") else ""
                                ),
                                "time": move_line.time if hasattr(move_line, "time") else "",
                                "user_operator_id": (
                                    move_line.user_operator_id.id
                                    if hasattr(move_line, "user_operator_id") and move_line.user_operator_id
                                    else 0
                                ),
                                "maneja_temperatura": maneja_temperatura,
                                "temperatura": (
                                    move_line.temperature if hasattr(move_line, "temperature") else 0
                                ),
                                "image": (
                                    f"{base_url}/api/view_imagen_linea_recepcion/{move_line.id}"
                                    if hasattr(move_line, "imagen") and move_line.imagen
                                    else ""
                                ),
                                "image_novedad": (
                                    f"{base_url}/api/view_imagen_observation/{move_line.id}"
                                    if hasattr(move_line, "imagen_observation")
                                    and move_line.imagen_observation
                                    else ""
                                ),
                            }

                            # Agregar información del lote si existe
                            if move_line.lot_id:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": move_line.lot_id.id,
                                        "lot_name": move_line.lot_id.name,
                                        "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                                    }
                                )
                            elif move_line.lot_name:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": 0,
                                        "lot_name": move_line.lot_name,
                                        "fecha_vencimiento": "",
                                    }
                                )

                            recepcion_info["lineas_recepcion_enviadas"].append(linea_enviada_info)

                    # Solo añadir recepciones que tengan líneas pendientes
                    # if recepcion_info["lineas_recepcion"]:
                    #     recepcion_info["numero_lineas"] = len(recepcion_info["lineas_recepcion"])
                    #     recepcion_info["numero_items"] = sum(linea["quantity_to_receive"] for linea in recepcion_info["lineas_recepcion"])

                    # array_recepciones.append(recepcion_info)

                    array_recepciones.append(recepcion_info)

            return {"code": 200, "update_version": update_required, "result": array_recepciones}

        except AccessError as e:
            return {
                "code": 403,
                "update_version": update_required,
                "msg": f"Acceso denegado: {str(e)}",
            }
        except Exception as err:
            return {
                "code": 400,
                "update_version": update_required,
                "msg": f"Error inesperado: {str(err)}",
            }

    @http.route("/api/recepciones/v2", auth="user", type="json", methods=["GET"])
    def get_recepciones_v2(self, **kwargs):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_recepciones = []

            base_url = request.httprequest.host_url.rstrip("/")

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # Obtener la configuración actual
            config = request.env["res.config.settings"].sudo().create({})
            consigna_habilitada = config.group_stock_tracking_owner

            # ✅ Obtener recepciones pendientes directamente de los almacenes permitidos
            for warehouse in allowed_warehouses:
                # Buscar todas las recepciones pendientes (no completadas ni canceladas) para este almacén
                recepciones_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "in", ["assigned", "confirmed"]),
                            ("picking_type_code", "=", "incoming"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("picking_type_id.sequence_code", "in", ["IN", "EE"]),
                            # ("is_return_picking", "=", False),
                            # ("user_id", "in", [user.id, False]),  # Asignadas al usuario o sin asignar
                            (
                                "responsable_id",
                                "in",
                                [user.id, False],
                            ),  # Asignadas al usuario o sin asignar
                        ]
                    )
                )

                for picking in recepciones_pendientes:
                    # Verificar si hay movimientos pendientes
                    # En Odoo 17, move_lines se cambió a move_ids o move_ids_without_package
                    # movimientos_pendientes = picking.move_ids.filtered(lambda m: m.state in ["confirmed", "assigned"])
                    movimientos_pendientes = picking.move_ids

                    # Si no hay movimientos pendientes, omitir esta recepción
                    if not movimientos_pendientes:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = (
                        picking.picking_type_id.create_backorder
                        if hasattr(picking.picking_type_id, "create_backorder")
                        else False
                    )

                    # Obtener la orden de compra relacionada (si existe)
                    purchase_order = picking.purchase_id or (
                        picking.origin
                        and request.env["purchase.order"]
                        .sudo()
                        .search([("name", "=", picking.origin)], limit=1)
                    )

                    # Calcular peso total - cambiando product_qty por product_uom_qty
                    peso_total = sum(
                        move.product_id.weight * move.product_uom_qty
                        for move in movimientos_pendientes
                        if move.product_id.weight
                    )

                    # Calcular número de ítems (suma total de cantidades)
                    numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

                    manejo_temperatura = False

                    productos_con_temperatura = picking.move_ids.mapped("product_id").filtered(
                        lambda p: hasattr(p, "temperature_control") and p.temperature_control
                    )
                    if productos_con_temperatura:
                        manejo_temperatura = True

                    # Obtener el ID del propietario
                    owner_id = picking.owner_id.id if hasattr(picking, "owner_id") and picking.owner_id else 0

                    # Obtener el nombre del propietario (si existe el ID)
                    propietario_nombre = ""
                    if owner_id:
                        partner = request.env["res.partner"].sudo().browse(owner_id)
                        propietario_nombre = partner.name if partner else ""

                    recepcion_info = {
                        "id": picking.id,
                        "name": picking.name,  # Nombre de la recepción
                        "fecha_creacion": picking.create_date,  # Fecha con hora
                        "proveedor_id": picking.partner_id.id or 0,
                        "proveedor": picking.partner_id.name or "",
                        "location_dest_id": picking.location_dest_id.id or "",
                        "location_dest_name": picking.location_dest_id.display_name or "",
                        "purchase_order_id": purchase_order.id if purchase_order else 0,
                        "purchase_order_name": (
                            purchase_order.name if purchase_order else ""
                        ),  # Orden de compra
                        "numero_entrada": picking.name or "",  # Número de entrada
                        "peso_total": peso_total,  # Peso total
                        "numero_lineas": len(picking.move_ids),  # Número de líneas (productos)
                        "numero_items": numero_items,  # Número de ítems (cantidades)
                        "state": picking.state,
                        "create_backorder": create_backorder,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "responsable_id": (picking.responsable_id.id if picking.responsable_id else 0),
                        "responsable": (picking.responsable_id.name if picking.responsable_id else ""),
                        "picking_type": picking.picking_type_id.name,
                        "backorder_id": picking.backorder_id.id if picking.backorder_id else 0,
                        "backorder_name": (
                            picking.backorder_id.name if picking.backorder_id else ""
                        ),  # Nombre del backorder
                        # Verificar si los campos personalizados existen
                        "start_time_reception": picking.start_time_reception or "",
                        "end_time_reception": picking.end_time_reception or "",
                        "picking_type_code": picking.picking_type_code,
                        "show_check_availability": (
                            picking.show_check_availability
                            if hasattr(picking, "show_check_availability")
                            else False
                        ),
                        "maneja_temperatura": manejo_temperatura,
                        "temperatura": (picking.temperature if hasattr(picking, "temperature") else 0),
                        "manejo_propetario": consigna_habilitada,
                        "propetario": propietario_nombre,
                        "lineas_recepcion": [],
                        "lineas_recepcion_enviadas": [],
                    }

                    # ✅ Procesar solo las líneas pendientes
                    for move in movimientos_pendientes:
                        product = move.product_id
                        purchase_line = move.purchase_line_id

                        cantidad_faltante = move.product_uom_qty - sum(
                            l.quantity for l in move.move_line_ids if l.is_done_item
                        )

                        novedad_bloqueante = move.move_line_ids.filtered(
                            lambda ml: ml.new_observation
                            and ml.new_observation.strip()
                            and ml.new_observation.strip().lower() != "sin novedad"
                        )

                        maneja_temperatura = (
                            product.temperature_control if hasattr(product, "temperature_control") else False
                        )
                        temperatura = move.temperature if hasattr(move, "temperature") else 0
                        imagen = move.imagen if hasattr(move, "imagen") else ""

                        # Obtener cantidad ordenada
                        quantity_ordered = 0
                        if purchase_line and purchase_line.product_uom_qty:
                            quantity_ordered = purchase_line.product_uom_qty
                        else:
                            quantity_ordered = move.product_uom_qty

                        # En Odoo 17, quantity_done ya no existe, se usa quantity
                        quantity_done = move.quantity

                        # # ⚠️ Saltar líneas totalmente recepcionadas
                        # if quantity_done < quantity_ordered:

                        if not move.picked:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search(
                                    [("product_id", "=", product.id)],
                                    order="expiration_date asc",
                                    limit=1,
                                )
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": (
                                    product.expiration_time if hasattr(product, "expiration_time") else ""
                                ),
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": array_packing,
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                                "maneja_temperatura": maneja_temperatura,
                                "temperatura": temperatura,
                                # "imagen": imagen,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        # necesito que se valide que si en move_line_ids no hay novedades diferentes a Sin novedad o diferente a vacio. Por ejemplo si la novedad es diferente a "Sin novedad" o está vacía, entonces se debe considerar como una línea pendiente.
                        elif cantidad_faltante > 0 and not novedad_bloqueante:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search(
                                    [("product_id", "=", product.id)],
                                    order="expiration_date asc",
                                    limit=1,
                                )
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": (
                                    product.expiration_time if hasattr(product, "expiration_time") else ""
                                ),
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": array_packing,
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                                "maneja_temperatura": maneja_temperatura,
                                "temperatura": temperatura,
                                # "imagen": imagen,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        # ✅ Agregar las líneas de move_line que tengan is_done_item en True
                        # Verificación para campos personalizados

                        move_lines_done = move.move_line_ids.filtered(lambda ml: ml.is_done_item)
                        for move_line in move_lines_done:
                            cantidad_faltante = move.product_uom_qty - move_line.quantity

                            # Crear información de la línea enviada
                            linea_enviada_info = {
                                "id": move_line.id,
                                "id_move_line": move_line.id,
                                "id_move": move_line.id,
                                # "id_move": move.id,
                                "state": move_line.state,
                                "id_recepcion": picking.id,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                "quantity_done": move_line.quantity,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
                                "location_dest_id": move_line.location_dest_id.id or 0,
                                "location_dest_name": move_line.location_dest_id.display_name or "",
                                "location_dest_barcode": move_line.location_dest_id.barcode or "",
                                "location_id": move_line.location_id.id or 0,
                                "location_name": move_line.location_id.display_name or "",
                                "location_barcode": move_line.location_id.barcode or "",
                                # Campos personalizados con manejo de fallback
                                "is_done_item": (
                                    move_line.is_done_item
                                    if hasattr(move_line, "is_done_item")
                                    else (move_line.quantity > 0)
                                ),
                                "date_transaction": (
                                    move_line.date_transaction
                                    if hasattr(move_line, "date_transaction")
                                    else ""
                                ),
                                "observation": (
                                    move_line.new_observation if hasattr(move_line, "new_observation") else ""
                                ),
                                "time": move_line.time if hasattr(move_line, "time") else "",
                                "user_operator_id": (
                                    move_line.user_operator_id.id
                                    if hasattr(move_line, "user_operator_id") and move_line.user_operator_id
                                    else 0
                                ),
                                "maneja_temperatura": maneja_temperatura,
                                "temperatura": (
                                    move_line.temperature if hasattr(move_line, "temperature") else 0
                                ),
                                "image": (
                                    f"{base_url}/api/view_imagen_linea_recepcion/{move_line.id}"
                                    if hasattr(move_line, "imagen") and move_line.imagen
                                    else ""
                                ),
                                "image_novedad": (
                                    f"{base_url}/api/view_imagen_observation/{move_line.id}"
                                    if hasattr(move_line, "imagen_observation")
                                    and move_line.imagen_observation
                                    else ""
                                ),
                            }

                            # Agregar información del lote si existe
                            if move_line.lot_id:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": move_line.lot_id.id,
                                        "lot_name": move_line.lot_id.name,
                                        "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                                    }
                                )
                            elif move_line.lot_name:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": 0,
                                        "lot_name": move_line.lot_name,
                                        "fecha_vencimiento": "",
                                    }
                                )

                            recepcion_info["lineas_recepcion_enviadas"].append(linea_enviada_info)

                    # Solo añadir recepciones que tengan líneas pendientes
                    # if recepcion_info["lineas_recepcion"]:
                    #     recepcion_info["numero_lineas"] = len(recepcion_info["lineas_recepcion"])
                    #     recepcion_info["numero_items"] = sum(linea["quantity_to_receive"] for linea in recepcion_info["lineas_recepcion"])

                    # array_recepciones.append(recepcion_info)

                    array_recepciones.append(recepcion_info)

            return {"code": 200, "result": array_recepciones}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET OBTENER LAS DEVOLUCIONES PENDIENTES
    @http.route("/api/recepciones/devs", auth="user", type="json", methods=["GET"])
    def get_recepciones_devs(self, **kwargs):
        try:
            version_app = kwargs.get("version_app")

            # 1. Llama al otro método DENTRO de esta misma clase
            response_version = self.get_last_version()

            # 2. Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # 3. Compara las versiones
            update_required = False
            if version_app:
                app_parts = list(map(int, version_app.split(".")))
                latest_parts = list(map(int, latest_version_str.split(".")))
                if app_parts < latest_parts:
                    update_required = True
            else:
                update_required = True

            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Usuario no encontrado",
                }

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_recepciones = []

            # Generar URLs para ver la imagen

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # ✅ Obtener recepciones pendientes directamente de los almacenes permitidos
            for warehouse in allowed_warehouses:
                # Buscar todas las recepciones pendientes (no completadas ni canceladas) para este almacén
                recepciones_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "in", ["assigned", "confirmed"]),
                            ("picking_type_code", "=", "incoming"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("picking_type_id.sequence_code", "in", ["DEV"]),
                            # ("is_return_picking", "=", False),
                            # ("user_id", "in", [user.id, False]),  # Asignadas al usuario o sin asignar
                            (
                                "responsable_id",
                                "in",
                                [user.id, False],
                            ),  # Asignadas al usuario o sin asignar
                        ]
                    )
                )

                for picking in recepciones_pendientes:
                    # Verificar si hay movimientos pendientes
                    # En Odoo 17, move_lines se cambió a move_ids o move_ids_without_package
                    movimientos_pendientes = picking.move_ids.filtered(
                        lambda m: m.state in ["confirmed", "assigned"]
                    )

                    # Si no hay movimientos pendientes, omitir esta recepción
                    if not movimientos_pendientes:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = (
                        picking.picking_type_id.create_backorder
                        if hasattr(picking.picking_type_id, "create_backorder")
                        else False
                    )

                    # Obtener la orden de compra relacionada (si existe)
                    purchase_order = picking.purchase_id or (
                        picking.origin
                        and request.env["purchase.order"]
                        .sudo()
                        .search([("name", "=", picking.origin)], limit=1)
                    )

                    # Calcular peso total - cambiando product_qty por product_uom_qty
                    peso_total = sum(
                        move.product_id.weight * move.product_uom_qty
                        for move in movimientos_pendientes
                        if move.product_id.weight
                    )

                    # Calcular número de ítems (suma total de cantidades)
                    numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

                    manejo_temperatura = False

                    productos_con_temperatura = picking.move_ids.mapped("product_id").filtered(
                        lambda p: hasattr(p, "temperature_control") and p.temperature_control
                    )
                    if productos_con_temperatura:
                        manejo_temperatura = True

                    recepcion_info = {
                        "id": picking.id,
                        "name": picking.name,  # Nombre de la recepción
                        "fecha_creacion": picking.create_date,  # Fecha con hora
                        "proveedor_id": picking.partner_id.id or 0,
                        "proveedor": picking.partner_id.name or "",
                        "location_dest_id": picking.location_dest_id.id or "",
                        "location_dest_name": picking.location_dest_id.display_name or "",
                        "purchase_order_id": purchase_order.id if purchase_order else 0,
                        "purchase_order_name": (
                            purchase_order.name if purchase_order else ""
                        ),  # Orden de compra
                        "numero_entrada": picking.name or "",  # Número de entrada
                        "peso_total": peso_total,  # Peso total
                        "numero_lineas": 0,  # Número de líneas (productos)
                        "numero_items": 0,  # Número de ítems (cantidades)
                        "state": picking.state,
                        "create_backorder": create_backorder,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "responsable_id": (picking.responsable_id.id if picking.responsable_id else 0),
                        "responsable": (picking.responsable_id.name if picking.responsable_id else ""),
                        "picking_type": picking.picking_type_id.name,
                        "backorder_id": picking.backorder_id.id if picking.backorder_id else 0,
                        "backorder_name": (
                            picking.backorder_id.name if picking.backorder_id else ""
                        ),  # Nombre del backorder
                        # Verificar si los campos personalizados existen
                        "start_time_reception": picking.start_time_reception or "",
                        "end_time_reception": picking.end_time_reception or "",
                        "picking_type_code": picking.picking_type_code,
                        "show_check_availability": (
                            picking.show_check_availability
                            if hasattr(picking, "show_check_availability")
                            else False
                        ),
                        "maneja_temperatura": manejo_temperatura,
                        "temperatura": (picking.temperature if hasattr(picking, "temperature") else 0),
                        "lineas_recepcion": [],
                        "lineas_recepcion_enviadas": [],
                    }

                    # ✅ Procesar solo las líneas pendientes
                    for move in movimientos_pendientes:
                        product = move.product_id
                        purchase_line = move.purchase_line_id

                        cantidad_faltante = move.product_uom_qty - sum(
                            l.quantity for l in move.move_line_ids if l.is_done_item
                        )

                        # Obtener cantidad ordenada
                        quantity_ordered = 0
                        if purchase_line and purchase_line.product_uom_qty:
                            quantity_ordered = purchase_line.product_uom_qty
                        else:
                            quantity_ordered = move.product_uom_qty

                        # En Odoo 17, quantity_done ya no existe, se usa quantity
                        quantity_done = move.quantity

                        # # ⚠️ Saltar líneas totalmente recepcionadas
                        # if quantity_done < quantity_ordered:

                        if not move.picked:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search(
                                    [("product_id", "=", product.id)],
                                    order="expiration_date asc",
                                    limit=1,
                                )
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": (
                                    product.expiration_time if hasattr(product, "expiration_time") else ""
                                ),
                                "use_expiration_date": (
                                    product.use_expiration_date
                                    if hasattr(product, "use_expiration_date")
                                    else False
                                ),
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": array_packing,
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        elif cantidad_faltante > 0:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search(
                                    [("product_id", "=", product.id)],
                                    order="expiration_date asc",
                                    limit=1,
                                )
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": (
                                    product.expiration_time if hasattr(product, "expiration_time") else ""
                                ),
                                "use_expiration_date": (
                                    product.use_expiration_date
                                    if hasattr(product, "use_expiration_date")
                                    else False
                                ),
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": array_packing,
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        # ✅ Agregar las líneas de move_line que tengan is_done_item en True
                        # Verificación para campos personalizados

                        move_lines_done = move.move_line_ids.filtered(lambda ml: ml.is_done_item)
                        for move_line in move_lines_done:
                            cantidad_faltante = move.product_uom_qty - move_line.quantity

                            # Crear información de la línea enviada
                            linea_enviada_info = {
                                "id": move_line.id,
                                "id_move_line": move_line.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "use_expiration_date": (
                                    product.use_expiration_date
                                    if hasattr(product, "use_expiration_date")
                                    else False
                                ),
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                "quantity_done": move_line.quantity,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
                                "location_dest_id": move_line.location_dest_id.id or 0,
                                "location_dest_name": move_line.location_dest_id.display_name or "",
                                "location_dest_barcode": move_line.location_dest_id.barcode or "",
                                "location_id": move_line.location_id.id or 0,
                                "location_name": move_line.location_id.display_name or "",
                                "location_barcode": move_line.location_id.barcode or "",
                                # Campos personalizados con manejo de fallback
                                "is_done_item": (
                                    move_line.is_done_item
                                    if hasattr(move_line, "is_done_item")
                                    else (move_line.quantity > 0)
                                ),
                                "date_transaction": (
                                    move_line.date_transaction
                                    if hasattr(move_line, "date_transaction")
                                    else ""
                                ),
                                "observation": (
                                    move_line.new_observation if hasattr(move_line, "new_observation") else ""
                                ),
                                "time": move_line.time if hasattr(move_line, "time") else "",
                                "user_operator_id": (
                                    move_line.user_operator_id.id
                                    if hasattr(move_line, "user_operator_id") and move_line.user_operator_id
                                    else 0
                                ),
                            }

                            # Agregar información del lote si existe
                            if move_line.lot_id:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": move_line.lot_id.id,
                                        "lot_name": move_line.lot_id.name,
                                        "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                                    }
                                )
                            elif move_line.lot_name:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": 0,
                                        "lot_name": move_line.lot_name,
                                        "fecha_vencimiento": "",
                                    }
                                )

                            recepcion_info["lineas_recepcion_enviadas"].append(linea_enviada_info)

                    # Solo añadir recepciones que tengan líneas pendientes
                    if recepcion_info["lineas_recepcion"]:
                        recepcion_info["numero_lineas"] = len(recepcion_info["lineas_recepcion"])
                        recepcion_info["numero_items"] = sum(
                            linea["quantity_to_receive"] for linea in recepcion_info["lineas_recepcion"]
                        )

                        array_recepciones.append(recepcion_info)

            return {"code": 200, "update_version": update_required, "result": array_recepciones}

        except AccessError as e:
            return {
                "code": 403,
                "update_version": update_required,
                "msg": f"Acceso denegado: {str(e)}",
            }
        except Exception as err:
            return {
                "code": 400,
                "update_version": update_required,
                "msg": f"Error inesperado: {str(err)}",
            }

    @http.route("/api/recepciones/devs/v2", auth="user", type="json", methods=["GET"])
    def get_recepciones_devs_v2(self, **kwargs):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_recepciones = []

            # Generar URLs para ver la imagen

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # ✅ Obtener recepciones pendientes directamente de los almacenes permitidos
            for warehouse in allowed_warehouses:
                # Buscar todas las recepciones pendientes (no completadas ni canceladas) para este almacén
                recepciones_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "in", ["assigned", "confirmed"]),
                            ("picking_type_code", "=", "incoming"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("picking_type_id.sequence_code", "in", ["DEV"]),
                            # ("is_return_picking", "=", False),
                            # ("user_id", "in", [user.id, False]),  # Asignadas al usuario o sin asignar
                            (
                                "responsable_id",
                                "in",
                                [user.id, False],
                            ),  # Asignadas al usuario o sin asignar
                        ]
                    )
                )

                for picking in recepciones_pendientes:
                    # Verificar si hay movimientos pendientes
                    # En Odoo 17, move_lines se cambió a move_ids o move_ids_without_package
                    movimientos_pendientes = picking.move_ids.filtered(
                        lambda m: m.state in ["confirmed", "assigned"]
                    )

                    # Si no hay movimientos pendientes, omitir esta recepción
                    if not movimientos_pendientes:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = (
                        picking.picking_type_id.create_backorder
                        if hasattr(picking.picking_type_id, "create_backorder")
                        else False
                    )

                    # Obtener la orden de compra relacionada (si existe)
                    purchase_order = picking.purchase_id or (
                        picking.origin
                        and request.env["purchase.order"]
                        .sudo()
                        .search([("name", "=", picking.origin)], limit=1)
                    )

                    # Calcular peso total - cambiando product_qty por product_uom_qty
                    peso_total = sum(
                        move.product_id.weight * move.product_uom_qty
                        for move in movimientos_pendientes
                        if move.product_id.weight
                    )

                    # Calcular número de ítems (suma total de cantidades)
                    numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

                    manejo_temperatura = False

                    productos_con_temperatura = picking.move_ids.mapped("product_id").filtered(
                        lambda p: hasattr(p, "temperature_control") and p.temperature_control
                    )
                    if productos_con_temperatura:
                        manejo_temperatura = True

                    recepcion_info = {
                        "id": picking.id,
                        "name": picking.name,  # Nombre de la recepción
                        "fecha_creacion": picking.create_date,  # Fecha con hora
                        "proveedor_id": picking.partner_id.id or 0,
                        "proveedor": picking.partner_id.name or "",
                        "location_dest_id": picking.location_dest_id.id or "",
                        "location_dest_name": picking.location_dest_id.display_name or "",
                        "purchase_order_id": purchase_order.id if purchase_order else 0,
                        "purchase_order_name": (
                            purchase_order.name if purchase_order else ""
                        ),  # Orden de compra
                        "numero_entrada": picking.name or "",  # Número de entrada
                        "peso_total": peso_total,  # Peso total
                        "numero_lineas": 0,  # Número de líneas (productos)
                        "numero_items": 0,  # Número de ítems (cantidades)
                        "state": picking.state,
                        "create_backorder": create_backorder,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "responsable_id": (picking.responsable_id.id if picking.responsable_id else 0),
                        "responsable": (picking.responsable_id.name if picking.responsable_id else ""),
                        "picking_type": picking.picking_type_id.name,
                        "backorder_id": picking.backorder_id.id if picking.backorder_id else 0,
                        "backorder_name": (
                            picking.backorder_id.name if picking.backorder_id else ""
                        ),  # Nombre del backorder
                        # Verificar si los campos personalizados existen
                        "start_time_reception": picking.start_time_reception or "",
                        "end_time_reception": picking.end_time_reception or "",
                        "picking_type_code": picking.picking_type_code,
                        "show_check_availability": (
                            picking.show_check_availability
                            if hasattr(picking, "show_check_availability")
                            else False
                        ),
                        "maneja_temperatura": manejo_temperatura,
                        "temperatura": (picking.temperature if hasattr(picking, "temperature") else 0),
                        "lineas_recepcion": [],
                        "lineas_recepcion_enviadas": [],
                    }

                    # ✅ Procesar solo las líneas pendientes
                    for move in movimientos_pendientes:
                        product = move.product_id
                        purchase_line = move.purchase_line_id

                        cantidad_faltante = move.product_uom_qty - sum(
                            l.quantity for l in move.move_line_ids if l.is_done_item
                        )

                        # Obtener cantidad ordenada
                        quantity_ordered = 0
                        if purchase_line and purchase_line.product_uom_qty:
                            quantity_ordered = purchase_line.product_uom_qty
                        else:
                            quantity_ordered = move.product_uom_qty

                        # En Odoo 17, quantity_done ya no existe, se usa quantity
                        quantity_done = move.quantity

                        # # ⚠️ Saltar líneas totalmente recepcionadas
                        # if quantity_done < quantity_ordered:

                        if not move.picked:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search(
                                    [("product_id", "=", product.id)],
                                    order="expiration_date asc",
                                    limit=1,
                                )
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": (
                                    product.expiration_time if hasattr(product, "expiration_time") else ""
                                ),
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": array_packing,
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        elif cantidad_faltante > 0:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search(
                                    [("product_id", "=", product.id)],
                                    order="expiration_date asc",
                                    limit=1,
                                )
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": (
                                    product.expiration_time if hasattr(product, "expiration_time") else ""
                                ),
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": array_packing,
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        # ✅ Agregar las líneas de move_line que tengan is_done_item en True
                        # Verificación para campos personalizados

                        move_lines_done = move.move_line_ids.filtered(lambda ml: ml.is_done_item)
                        for move_line in move_lines_done:
                            cantidad_faltante = move.product_uom_qty - move_line.quantity

                            # Crear información de la línea enviada
                            linea_enviada_info = {
                                "id": move_line.id,
                                "id_move_line": move_line.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "product_id": product.id,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "quantity_ordered": (
                                    purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                                ),
                                "quantity_to_receive": move.product_uom_qty,
                                "quantity_done": move_line.quantity,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
                                "location_dest_id": move_line.location_dest_id.id or 0,
                                "location_dest_name": move_line.location_dest_id.display_name or "",
                                "location_dest_barcode": move_line.location_dest_id.barcode or "",
                                "location_id": move_line.location_id.id or 0,
                                "location_name": move_line.location_id.display_name or "",
                                "location_barcode": move_line.location_id.barcode or "",
                                # Campos personalizados con manejo de fallback
                                "is_done_item": (
                                    move_line.is_done_item
                                    if hasattr(move_line, "is_done_item")
                                    else (move_line.quantity > 0)
                                ),
                                "date_transaction": (
                                    move_line.date_transaction
                                    if hasattr(move_line, "date_transaction")
                                    else ""
                                ),
                                "observation": (
                                    move_line.new_observation if hasattr(move_line, "new_observation") else ""
                                ),
                                "time": move_line.time if hasattr(move_line, "time") else "",
                                "user_operator_id": (
                                    move_line.user_operator_id.id
                                    if hasattr(move_line, "user_operator_id") and move_line.user_operator_id
                                    else 0
                                ),
                            }

                            # Agregar información del lote si existe
                            if move_line.lot_id:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": move_line.lot_id.id,
                                        "lot_name": move_line.lot_id.name,
                                        "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                                    }
                                )
                            elif move_line.lot_name:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": 0,
                                        "lot_name": move_line.lot_name,
                                        "fecha_vencimiento": "",
                                    }
                                )

                            recepcion_info["lineas_recepcion_enviadas"].append(linea_enviada_info)

                    # Solo añadir recepciones que tengan líneas pendientes
                    if recepcion_info["lineas_recepcion"]:
                        recepcion_info["numero_lineas"] = len(recepcion_info["lineas_recepcion"])
                        recepcion_info["numero_items"] = sum(
                            linea["quantity_to_receive"] for linea in recepcion_info["lineas_recepcion"]
                        )

                        array_recepciones.append(recepcion_info)

            return {"code": 200, "result": array_recepciones}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET OBTENER LAS DEVOLUCIONES POR BATCH
    @http.route("/api/recepciones/batchs", auth="user", type="json", methods=["GET"])
    def get_recepciones_batch(self, **kwargs):
        try:
            version_app = kwargs.get("version_app")

            # 1. Llama al otro método DENTRO de esta misma clase
            response_version = self.get_last_version()

            # 2. Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # 3. Compara las versiones
            update_required = False
            if version_app:
                app_parts = list(map(int, version_app.split(".")))
                latest_parts = list(map(int, latest_version_str.split(".")))
                if app_parts < latest_parts:
                    update_required = True
            else:
                update_required = True

            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Criterios de búsqueda para los lotes
            search_domain = [
                ("state", "=", "in_progress"),
                ("picking_type_code", "=", "incoming"),
                ("user_id", "in", [user.id, False]),
            ]

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search(search_domain)

            # ✅ Verificar si no hay lotes encontrados
            if not batchs:
                return {
                    "code": 200,
                    "update_version": update_required,
                    "msg": "No tienes batches asignados",
                    "result": [],
                }

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos de línea de stock en vez de move.line.unified
                # Cambio 1: Usar stock.move.line en lugar de move.line.unified
                move_line_ids = (
                    request.env["stock.move.line"]
                    .sudo()
                    .search(
                        [
                            (
                                "picking_id.batch_id",
                                "=",
                                batch.id,
                            ),  # Cambio 2: Referencia a batch a través de picking_id
                            # ("location_id", "in", user_location_ids),
                            (
                                "is_done_item_pack",
                                "=",
                                False,
                            ),  # Cambio 3: Usar el campo personalizado is_done_item_pack
                        ]
                    )
                )

                if not move_line_ids:
                    continue

                # Verificar si hay pickings y obtener orígenes
                origins_list = []
                if batch.picking_ids:
                    for picking in batch.picking_ids:
                        if picking.origin:
                            origins_list.append(
                                {
                                    "name": picking.origin,
                                    "id": picking.id,
                                    "id_batch": batch.id,
                                }
                            )

                # ✅ Leer detalles de los movimientos
                stock_moves = move_line_ids.read()

                # ✅ Crear la información básica del batch
                batch_info = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "fecha_creacion": batch.create_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.id if batch.picking_type_id else 0,
                    "picking_type": (batch.picking_type_id.display_name if batch.picking_type_id else "N/A"),
                    "picking_type_code": "incoming",  # Similar al endpoint de recepciones
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "location_id": batch.location_id.id if batch.location_id else 0,
                    "location_name": (batch.location_id.display_name if batch.location_id else "SIN-MUELLE"),
                    "location_barcode": batch.location_id.barcode or "",
                    "warehouse_id": (
                        batch.picking_type_id.warehouse_id.id
                        if batch.picking_type_id and batch.picking_type_id.warehouse_id
                        else 0
                    ),
                    "warehouse_name": (
                        batch.picking_type_id.warehouse_id.name
                        if batch.picking_type_id and batch.picking_type_id.warehouse_id
                        else ""
                    ),
                    "numero_lineas": len(stock_moves),
                    "numero_items": sum(move["quantity"] for move in stock_moves),
                    "start_time_reception": batch.start_time_pick or "",
                    "end_time_reception": batch.end_time_pick or "",
                    "priority": batch.priority if hasattr(batch, "priority") else "",
                    "zona_entrega": (
                        batch.picking_ids[0].delivery_zone_id.name
                        if batch.picking_ids and batch.picking_ids[0].delivery_zone_id
                        else "SIN-ZONA"
                    ),
                    "origin": origins_list,
                    "responsable_id": batch.user_id.id or 0,
                    "responsable": batch.user_id.name or "",
                    "proveedor_id": batch.picking_ids[0].partner_id.id or 0,
                    "proveedor": batch.picking_ids[0].partner_id.name or "",
                    "location_dest_id": batch.picking_ids[0].location_dest_id.id or 0,
                    "location_dest_name": batch.picking_ids[0].location_dest_id.display_name or "",
                    "backorder_id": 0,
                    "backorder_name": "",
                    "purchase_order_id": batch.picking_ids[0].purchase_id.id or 0,
                    "purchase_order_name": batch.picking_ids[0].purchase_id.name or "",
                    "show_check_availability": (
                        batch.show_check_availability if hasattr(batch, "show_check_availability") else False
                    ),
                    "lineas_recepcion": [],  # Similar a lineas_recepcion
                    "lineas_recepcion_enviadas": [],  # Similar a lineas_recepcion_enviadas
                }

                # ✅ Precarga de productos y ubicaciones para optimizar
                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {
                    prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)
                }

                location_ids = {move["location_id"][0] for move in stock_moves}
                location_ids.update({move["location_dest_id"][0] for move in stock_moves})
                locations_dict = {
                    loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)
                }

                # ✅ Procesar cada movimiento
                for move in stock_moves:
                    product = products.get(move["product_id"][0])
                    location = locations_dict.get(move["location_id"][0])
                    location_dest = locations_dict.get(move["location_dest_id"][0])

                    # ✅ Obtener códigos de barras adicionales
                    array_barcodes = []
                    if hasattr(product, "barcode_ids") and product.barcode_ids:
                        array_barcodes = [
                            {
                                "barcode": barcode.name,
                                "id_move": move["id"],
                                "id_product": product.id,
                                "batch_id": batch.id,
                            }
                            for barcode in product.barcode_ids
                            if barcode.name
                        ]

                    # ✅ Obtener empaques del producto
                    array_packing = []
                    if hasattr(product, "packaging_ids") and product.packaging_ids:
                        array_packing = [
                            {
                                "barcode": pack.barcode,
                                "cantidad": pack.qty,
                                "id_move": move["id"],
                                "id_product": product.id,
                                "batch_id": batch.id,
                            }
                            for pack in product.packaging_ids
                            if pack.barcode
                        ]

                    # Cambio 5: En stock.move.line, el picking está directamente relacionado
                    picking = (
                        request.env["stock.picking"].sudo().browse(move["picking_id"][0])
                        if move.get("picking_id")
                        else None
                    )
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener información de zona de entrega
                    delivery_zone_id = (
                        picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0
                    )
                    delivery_zone_name = (
                        picking.delivery_zone_id.display_name
                        if picking and picking.delivery_zone_id
                        else "SIN-ZONA"
                    )

                    # ✅ Obtener información de lote y fecha de vencimiento
                    lot_id = move["lot_id"][0] if move["lot_id"] else 0
                    lot_name = move["lot_id"][1] if move["lot_id"] and len(move["lot_id"]) > 1 else ""
                    expiration_date = ""
                    if lot_id:
                        lot = request.env["stock.lot"].sudo().browse(lot_id)
                        if hasattr(lot, "expiration_date"):
                            expiration_date = lot.expiration_date

                    move_line_obj = request.env["stock.move.line"].sudo().browse(move["id"])

                    # Buscar líneas completadas relacionadas con el mismo move_id
                    if hasattr(move_line_obj, "move_id") and move_line_obj.move_id:
                        completed_lines = (
                            request.env["stock.move.line"]
                            .sudo()
                            .search(
                                [
                                    ("move_id", "=", move_line_obj.move_id.id),
                                    ("is_done_item_pack", "=", True),
                                ]
                            )
                        )
                        # Sumar la cantidad de las líneas completadas
                        completed_quantity = sum(line.quantity for line in completed_lines)
                        cantidad_faltante = move.get("quantity_demanded", 0) - completed_quantity
                    else:
                        # Si no hay move_id, usar el campo quantity directamente
                        cantidad_faltante = move.get("quantity_demanded", 0) - move.get("quantity", 0)

                    # ✅ Crear la línea del batch (similar a linea_recepcion)
                    batch_info["lineas_recepcion"].append(
                        {
                            "id": move["id"],
                            "id_move": move["id"],
                            "id_batch": batch.id,
                            "id_recepcion": batch.id,
                            # Cambio 7: Usar el state del move o del picking relacionado
                            "state": move.get("state", "assigned"),
                            "product_id": product.id or 0,
                            "product_name": product.display_name or "",
                            "product_code": product.default_code if product else "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking if product else "",
                            "fecha_vencimiento": expiration_date,
                            "dias_vencimiento": (
                                product.expiration_time if hasattr(product, "expiration_time") else ""
                            ),
                            "use_expiration_date": (
                                product.use_expiration_date
                                if hasattr(product, "use_expiration_date")
                                else False
                            ),
                            "other_barcodes": get_barcodes(product, move["id"], picking_id),
                            "product_packing": array_packing,
                            # Cambio 8: Usando quantity en lugar de product_uom_qty
                            "quantity_ordered": move["quantity"],
                            "cantidad_faltante": cantidad_faltante,
                            "quantity_to_receive": move["quantity"],
                            "uom": product.uom_id.name if product and product.uom_id else "UND",
                            "location_dest_id": move["location_dest_id"][0],
                            "location_dest_name": location_dest.display_name or "",
                            "location_dest_barcode": location_dest.barcode or "",
                            "location_id": move["location_id"][0],
                            "location_name": location.display_name if location else "",
                            "location_barcode": location.barcode or "",
                            "weight": product.weight if product else 0,
                            "rimoval_priority": (location.priority_picking_desplay if location else ""),
                            "lot_id": lot_id,
                            "lot_name": lot_name,
                            "zona_entrega": delivery_zone_name,
                            "id_zona_entrega": delivery_zone_id,
                            "picking_id": picking_id,
                            "picking_name": picking.display_name if picking else "",
                            "origin": picking.origin or "" if picking else "",
                        }
                    )

                # ✅ Buscar movimientos ya procesados (is_done_item_pack = True)
                # Cambio 9: Buscar stock.move.line completados usando campo personalizado
                done_move_line_ids = (
                    request.env["stock.move.line"]
                    .sudo()
                    .search([("picking_id.batch_id", "=", batch.id), ("is_done_item_pack", "=", True)])
                )  # Usando el campo personalizado is_done_item_pack

                # ✅ Procesar movimientos ya completados
                if done_move_line_ids:
                    done_stock_moves = done_move_line_ids.read()

                    for done_move in done_stock_moves:
                        product = (
                            products.get(done_move["product_id"][0]) if done_move["product_id"] else None
                        )

                        product = (
                            obtener_info_producto(done_move["product_id"][0])
                            if done_move.get("product_id")
                            else None
                        )
                        # location = locations_dict.get(done_move["location_id"][0]) if done_move["location_id"] else None
                        # location_dest = locations_dict.get(done_move["location_dest_id"][0]) if done_move["location_dest_id"] else None

                        # location_dest = request.env["stock.location"].sudo().browse(done_move["location_dest_id"][0]) if done_move.get("location_dest_id") else None

                        location_dest = (
                            obtener_info_ubicacion(done_move["location_dest_id"][0])
                            if done_move.get("location_dest_id")
                            else None
                        )
                        location = (
                            obtener_info_ubicacion(done_move["location_id"][0])
                            if done_move.get("location_id")
                            else None
                        )

                        # Información del lote
                        lot_id = done_move["lot_id"][0] if done_move.get("lot_id") else 0
                        lot_name = (
                            done_move["lot_id"][1]
                            if done_move.get("lot_id") and len(done_move["lot_id"]) > 1
                            else ""
                        )
                        expiration_date = ""

                        if lot_id:
                            lot = request.env["stock.lot"].sudo().browse(lot_id)
                            if hasattr(lot, "expiration_date"):
                                expiration_date = lot.expiration_date

                        # Obtener el picking asociado
                        picking = (
                            request.env["stock.picking"].sudo().browse(done_move["picking_id"][0])
                            if done_move.get("picking_id")
                            else None
                        )

                        # Cambio 10: Mapeo de campos de stock.move.line a la estructura esperada
                        batch_info["lineas_recepcion_enviadas"].append(
                            {
                                "id": done_move["id"],
                                "id_move_line": done_move["id"],
                                "id_move": done_move["id"],
                                "id_recepcion": batch.id,
                                "id_batch": batch.id,
                                "product_id": (done_move["product_id"][0] if done_move["product_id"] else 0),
                                "product_name": product.display_name or "",
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "use_expiration_date": (
                                    product.use_expiration_date
                                    if hasattr(product, "use_expiration_date")
                                    else False
                                ),
                                "quantity_ordered": done_move["quantity"],
                                "quantity_done": done_move["quantity"],
                                "uom": product.uom_id.name if product and product.uom_id else "UND",
                                "location_dest_id": location_dest.id if location_dest else 0,
                                "location_dest_name": (location_dest.display_name if location_dest else ""),
                                "location_dest_barcode": location_dest.barcode or "",
                                "location_id": location.id if location else 0,
                                "location_name": location.display_name if location else "",
                                "location_barcode": location.barcode or "",
                                "is_done_item": done_move.get(
                                    "is_done_item_pack", True
                                ),  # Usando el campo personalizado is_done_item_pack
                                "date_transaction": done_move.get("date_transaction_packing", ""),
                                "observation": done_move.get("new_observation_packing", ""),
                                "time": done_move.get("time_packing", ""),
                                "user_operator_id": done_move["user_operator_id"][0] or 0,
                                "lot_id": lot_id,
                                "lot_name": lot_name,
                                "fecha_vencimiento": expiration_date,
                            }
                        )

                # Solo añadir el batch si tiene líneas pendientes
                if batch_info["lineas_recepcion"]:
                    array_batch.append(batch_info)

            return {"code": 200, "update_version": update_required, "result": array_batch}

        except Exception as err:
            return {
                "code": 400,
                "update_version": update_required,
                "msg": f"Error inesperado: {str(err)}",
            }

    @http.route("/api/recepciones/batchs/v2", auth="user", type="json", methods=["GET"])
    def get_recepciones_batch_v2(self, **kwargs):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Criterios de búsqueda para los lotes
            search_domain = [
                ("state", "=", "in_progress"),
                ("picking_type_code", "=", "incoming"),
                ("user_id", "in", [user.id, False]),
            ]

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search(search_domain)

            # ✅ Verificar si no hay lotes encontrados
            if not batchs:
                return {"code": 200, "msg": "No tienes batches asignados", "result": []}

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos de línea de stock en vez de move.line.unified
                # Cambio 1: Usar stock.move.line en lugar de move.line.unified
                move_line_ids = (
                    request.env["stock.move.line"]
                    .sudo()
                    .search(
                        [
                            (
                                "picking_id.batch_id",
                                "=",
                                batch.id,
                            ),  # Cambio 2: Referencia a batch a través de picking_id
                            # ("location_id", "in", user_location_ids),
                            (
                                "is_done_item_pack",
                                "=",
                                False,
                            ),  # Cambio 3: Usar el campo personalizado is_done_item_pack
                        ]
                    )
                )

                if not move_line_ids:
                    continue

                # Verificar si hay pickings y obtener orígenes
                origins_list = []
                if batch.picking_ids:
                    for picking in batch.picking_ids:
                        if picking.origin:
                            origins_list.append(
                                {
                                    "name": picking.origin,
                                    "id": picking.id,
                                    "id_batch": batch.id,
                                }
                            )

                # ✅ Leer detalles de los movimientos
                stock_moves = move_line_ids.read()

                # ✅ Crear la información básica del batch
                batch_info = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "fecha_creacion": batch.create_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.id if batch.picking_type_id else 0,
                    "picking_type": (batch.picking_type_id.display_name if batch.picking_type_id else "N/A"),
                    "picking_type_code": "incoming",  # Similar al endpoint de recepciones
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "location_id": batch.location_id.id if batch.location_id else 0,
                    "location_name": (batch.location_id.display_name if batch.location_id else "SIN-MUELLE"),
                    "location_barcode": batch.location_id.barcode or "",
                    "warehouse_id": (
                        batch.picking_type_id.warehouse_id.id
                        if batch.picking_type_id and batch.picking_type_id.warehouse_id
                        else 0
                    ),
                    "warehouse_name": (
                        batch.picking_type_id.warehouse_id.name
                        if batch.picking_type_id and batch.picking_type_id.warehouse_id
                        else ""
                    ),
                    "numero_lineas": len(stock_moves),
                    "numero_items": sum(move["quantity"] for move in stock_moves),
                    "start_time_reception": batch.start_time_pick or "",
                    "end_time_reception": batch.end_time_pick or "",
                    "priority": batch.priority if hasattr(batch, "priority") else "",
                    "zona_entrega": (
                        batch.picking_ids[0].delivery_zone_id.name
                        if batch.picking_ids and batch.picking_ids[0].delivery_zone_id
                        else "SIN-ZONA"
                    ),
                    "origin": origins_list,
                    "responsable_id": batch.user_id.id or 0,
                    "responsable": batch.user_id.name or "",
                    "proveedor_id": batch.picking_ids[0].partner_id.id or 0,
                    "proveedor": batch.picking_ids[0].partner_id.name or "",
                    "location_dest_id": batch.picking_ids[0].location_dest_id.id or 0,
                    "location_dest_name": batch.picking_ids[0].location_dest_id.display_name or "",
                    "backorder_id": 0,
                    "backorder_name": "",
                    "purchase_order_id": batch.picking_ids[0].purchase_id.id or 0,
                    "purchase_order_name": batch.picking_ids[0].purchase_id.name or "",
                    "show_check_availability": (
                        batch.show_check_availability if hasattr(batch, "show_check_availability") else False
                    ),
                    "lineas_recepcion": [],  # Similar a lineas_recepcion
                    "lineas_recepcion_enviadas": [],  # Similar a lineas_recepcion_enviadas
                }

                # ✅ Precarga de productos y ubicaciones para optimizar
                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {
                    prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)
                }

                location_ids = {move["location_id"][0] for move in stock_moves}
                location_ids.update({move["location_dest_id"][0] for move in stock_moves})
                locations_dict = {
                    loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)
                }

                # ✅ Procesar cada movimiento
                for move in stock_moves:
                    product = products.get(move["product_id"][0])
                    location = locations_dict.get(move["location_id"][0])
                    location_dest = locations_dict.get(move["location_dest_id"][0])

                    # ✅ Obtener códigos de barras adicionales
                    array_barcodes = []
                    if hasattr(product, "barcode_ids") and product.barcode_ids:
                        array_barcodes = [
                            {
                                "barcode": barcode.name,
                                "id_move": move["id"],
                                "id_product": product.id,
                                "batch_id": batch.id,
                            }
                            for barcode in product.barcode_ids
                            if barcode.name
                        ]

                    # ✅ Obtener empaques del producto
                    array_packing = []
                    if hasattr(product, "packaging_ids") and product.packaging_ids:
                        array_packing = [
                            {
                                "barcode": pack.barcode,
                                "cantidad": pack.qty,
                                "id_move": move["id"],
                                "id_product": product.id,
                                "batch_id": batch.id,
                            }
                            for pack in product.packaging_ids
                            if pack.barcode
                        ]

                    # Cambio 5: En stock.move.line, el picking está directamente relacionado
                    picking = (
                        request.env["stock.picking"].sudo().browse(move["picking_id"][0])
                        if move.get("picking_id")
                        else None
                    )
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener información de zona de entrega
                    delivery_zone_id = (
                        picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0
                    )
                    delivery_zone_name = (
                        picking.delivery_zone_id.display_name
                        if picking and picking.delivery_zone_id
                        else "SIN-ZONA"
                    )

                    # ✅ Obtener información de lote y fecha de vencimiento
                    lot_id = move["lot_id"][0] if move["lot_id"] else 0
                    lot_name = move["lot_id"][1] if move["lot_id"] and len(move["lot_id"]) > 1 else ""
                    expiration_date = ""
                    if lot_id:
                        lot = request.env["stock.lot"].sudo().browse(lot_id)
                        if hasattr(lot, "expiration_date"):
                            expiration_date = lot.expiration_date

                    move_line_obj = request.env["stock.move.line"].sudo().browse(move["id"])

                    # Buscar líneas completadas relacionadas con el mismo move_id
                    if hasattr(move_line_obj, "move_id") and move_line_obj.move_id:
                        completed_lines = (
                            request.env["stock.move.line"]
                            .sudo()
                            .search(
                                [
                                    ("move_id", "=", move_line_obj.move_id.id),
                                    ("is_done_item_pack", "=", True),
                                ]
                            )
                        )
                        # Sumar la cantidad de las líneas completadas
                        completed_quantity = sum(line.quantity for line in completed_lines)
                        cantidad_faltante = move.get("quantity_demanded", 0) - completed_quantity
                    else:
                        # Si no hay move_id, usar el campo quantity directamente
                        cantidad_faltante = move.get("quantity_demanded", 0) - move.get("quantity", 0)

                    # ✅ Crear la línea del batch (similar a linea_recepcion)
                    batch_info["lineas_recepcion"].append(
                        {
                            "id": move["id"],
                            "id_move": move["id"],
                            "id_batch": batch.id,
                            "id_recepcion": batch.id,
                            # Cambio 7: Usar el state del move o del picking relacionado
                            "state": move.get("state", "assigned"),
                            "product_id": product.id or 0,
                            "product_name": product.display_name or "",
                            "product_code": product.default_code if product else "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking if product else "",
                            "fecha_vencimiento": expiration_date,
                            "dias_vencimiento": (
                                product.expiration_time if hasattr(product, "expiration_time") else ""
                            ),
                            "other_barcodes": get_barcodes(product, move["id"], picking_id),
                            "product_packing": array_packing,
                            # Cambio 8: Usando quantity en lugar de product_uom_qty
                            "quantity_ordered": move["quantity"],
                            "cantidad_faltante": cantidad_faltante,
                            "quantity_to_receive": move["quantity"],
                            "uom": product.uom_id.name if product and product.uom_id else "UND",
                            "location_dest_id": move["location_dest_id"][0],
                            "location_dest_name": location_dest.display_name or "",
                            "location_dest_barcode": location_dest.barcode or "",
                            "location_id": move["location_id"][0],
                            "location_name": location.display_name if location else "",
                            "location_barcode": location.barcode or "",
                            "weight": product.weight if product else 0,
                            "rimoval_priority": (location.priority_picking_desplay if location else ""),
                            "lot_id": lot_id,
                            "lot_name": lot_name,
                            "zona_entrega": delivery_zone_name,
                            "id_zona_entrega": delivery_zone_id,
                            "picking_id": picking_id,
                            "picking_name": picking.display_name if picking else "",
                            "origin": picking.origin or "" if picking else "",
                        }
                    )

                # ✅ Buscar movimientos ya procesados (is_done_item_pack = True)
                # Cambio 9: Buscar stock.move.line completados usando campo personalizado
                done_move_line_ids = (
                    request.env["stock.move.line"]
                    .sudo()
                    .search([("picking_id.batch_id", "=", batch.id), ("is_done_item_pack", "=", True)])
                )  # Usando el campo personalizado is_done_item_pack

                # ✅ Procesar movimientos ya completados
                if done_move_line_ids:
                    done_stock_moves = done_move_line_ids.read()

                    for done_move in done_stock_moves:
                        product = (
                            products.get(done_move["product_id"][0]) if done_move["product_id"] else None
                        )

                        product = (
                            obtener_info_producto(done_move["product_id"][0])
                            if done_move.get("product_id")
                            else None
                        )
                        # location = locations_dict.get(done_move["location_id"][0]) if done_move["location_id"] else None
                        # location_dest = locations_dict.get(done_move["location_dest_id"][0]) if done_move["location_dest_id"] else None

                        # location_dest = request.env["stock.location"].sudo().browse(done_move["location_dest_id"][0]) if done_move.get("location_dest_id") else None

                        location_dest = (
                            obtener_info_ubicacion(done_move["location_dest_id"][0])
                            if done_move.get("location_dest_id")
                            else None
                        )
                        location = (
                            obtener_info_ubicacion(done_move["location_id"][0])
                            if done_move.get("location_id")
                            else None
                        )

                        # Información del lote
                        lot_id = done_move["lot_id"][0] if done_move.get("lot_id") else 0
                        lot_name = (
                            done_move["lot_id"][1]
                            if done_move.get("lot_id") and len(done_move["lot_id"]) > 1
                            else ""
                        )
                        expiration_date = ""

                        if lot_id:
                            lot = request.env["stock.lot"].sudo().browse(lot_id)
                            if hasattr(lot, "expiration_date"):
                                expiration_date = lot.expiration_date

                        # Obtener el picking asociado
                        picking = (
                            request.env["stock.picking"].sudo().browse(done_move["picking_id"][0])
                            if done_move.get("picking_id")
                            else None
                        )

                        # Cambio 10: Mapeo de campos de stock.move.line a la estructura esperada
                        batch_info["lineas_recepcion_enviadas"].append(
                            {
                                "id": done_move["id"],
                                "id_move_line": done_move["id"],
                                "id_move": done_move["id"],
                                "id_recepcion": batch.id,
                                "id_batch": batch.id,
                                "product_id": (done_move["product_id"][0] if done_move["product_id"] else 0),
                                "product_name": product.display_name or "",
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "quantity_ordered": done_move["quantity"],
                                "quantity_done": done_move["quantity"],
                                "uom": product.uom_id.name if product and product.uom_id else "UND",
                                "location_dest_id": location_dest.id if location_dest else 0,
                                "location_dest_name": (location_dest.display_name if location_dest else ""),
                                "location_dest_barcode": location_dest.barcode or "",
                                "location_id": location.id if location else 0,
                                "location_name": location.display_name if location else "",
                                "location_barcode": location.barcode or "",
                                "is_done_item": done_move.get(
                                    "is_done_item_pack", True
                                ),  # Usando el campo personalizado is_done_item_pack
                                "date_transaction": done_move.get("date_transaction_packing", ""),
                                "observation": done_move.get("new_observation_packing", ""),
                                "time": done_move.get("time_packing", ""),
                                "user_operator_id": done_move["user_operator_id"][0] or 0,
                                "lot_id": lot_id,
                                "lot_name": lot_name,
                                "fecha_vencimiento": expiration_date,
                            }
                        )

                # Solo añadir el batch si tiene líneas pendientes
                if batch_info["lineas_recepcion"]:
                    array_batch.append(batch_info)

            return {"code": 200, "result": array_batch}

        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Transaccion Recepcion por ID
    @http.route("/api/recepciones/<int:id>", auth="user", type="json", methods=["GET"])
    def get_recepcion_by_id(self, id):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # ✅ Validar ID
            if not id:
                return {"code": 400, "msg": "ID de recepción no válido"}

            # ✅ Buscar recepción por ID
            recepcion = (
                request.env["stock.picking"]
                .sudo()
                .search([("id", "=", id), ("picking_type_code", "=", "incoming")], limit=1)
            )

            # ✅ Validar recepción
            if not recepcion:
                return {"code": 400, "msg": "Recepción no encontrada"}

            # ✅ Verificar si el usuario tiene acceso al almacén de la recepción
            if (
                not user.has_group("stock.group_stock_manager")
                and user.allowed_warehouse_ids
                and recepcion.picking_type_id.warehouse_id not in user.allowed_warehouse_ids
            ):
                return {"code": 403, "msg": "Acceso denegado"}

            # ✅ Verificar si la recepción tiene movimientos pendientes
            # Cambio para Odoo 17: move_lines -> move_ids
            movimientos_pendientes = recepcion.move_ids.filtered(lambda m: m.state not in ["done", "cancel"])
            if not movimientos_pendientes:
                return {"code": 400, "msg": "La recepción no tiene movimientos pendientes"}

            # ✅ Obtener la orden de compra relacionada (si existe)
            purchase_order = recepcion.purchase_id or (
                recepcion.origin
                and request.env["purchase.order"].sudo().search([("name", "=", recepcion.origin)], limit=1)
            )

            # Calcular peso total
            # Cambio para Odoo 17: Verificación de product_id y weight
            peso_total = sum(
                move.product_id.weight * move.product_uom_qty
                for move in movimientos_pendientes
                if move.product_id and move.product_id.weight
            )

            # Calcular número de ítems (suma total de cantidades)
            # Cambio para Odoo 17: product_qty -> product_uom_qty
            numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

            # Generar información de la recepción
            recepcion_info = {
                "id": recepcion.id,
                "name": recepcion.name,  # Nombre de la recepción
                "fecha_creacion": recepcion.create_date,  # Fecha con hora
                "scheduled_date": recepcion.scheduled_date,  # Fecha programada
                "proveedor_id": recepcion.partner_id.id,
                "proveedor": recepcion.partner_id.name,  # Proveedor
                "location_dest_id": recepcion.location_dest_id.id,
                "location_dest_name": recepcion.location_dest_id.display_name,  # Ubicación destino
                "purchase_order_id": purchase_order.id if purchase_order else 0,
                "purchase_order_name": (purchase_order.name if purchase_order else ""),  # Orden de compra
                "numero_entrada": recepcion.name,  # Número de entrada
                "peso_total": peso_total,  # Peso total
                "numero_lineas": len(movimientos_pendientes),  # Número de líneas (productos)
                "numero_items": numero_items,  # Número de ítems (cantidades)
                "state": recepcion.state,
                "origin": recepcion.origin or "",
                "priority": recepcion.priority,
                "warehouse_id": recepcion.picking_type_id.warehouse_id.id,
                "warehouse_name": recepcion.picking_type_id.warehouse_id.name,
                "location_id": recepcion.location_id.id,
                "location_name": recepcion.location_id.display_name,
                "responsable_id": recepcion.user_id.id if recepcion.user_id else 0,
                "responsable": recepcion.user_id.name if recepcion.user_id else "",
                "picking_type": recepcion.picking_type_id.name,
                "lineas_recepcion": [],
            }

            # ✅ Procesar solo las líneas pendientes
            for move in movimientos_pendientes:
                product = move.product_id
                purchase_line = move.purchase_line_id

                # Obtener códigos de barras adicionales
                array_barcodes = []
                # Cambio para Odoo 17: Verificación de campos con hasattr
                if hasattr(product, "barcode_ids"):
                    array_barcodes = [
                        {
                            "barcode": barcode.name,
                            "id_move": move.id,
                            "id_product": product.id,
                            "batch_id": recepcion.id,
                        }
                        for barcode in product.barcode_ids
                        if barcode.name
                    ]

                # Obtener empaques del producto
                array_packing = []
                # Cambio para Odoo 17: Verificación de campos con hasattr y cambio de nombre
                if hasattr(product, "packaging_ids"):
                    array_packing = [
                        {
                            "barcode": pack.barcode,
                            "cantidad": pack.qty,
                            "id_move": move.id,
                            "id_product": product.id,
                            "batch_id": recepcion.id,
                        }
                        for pack in product.packaging_ids
                        if pack.barcode
                    ]

                # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                fecha_vencimiento = ""
                if hasattr(product, "tracking") and product.tracking == "lot":
                    lot_domain = [("product_id", "=", product.id)]
                    # Cambio para Odoo 17: Verificación del campo expiration_date
                    if hasattr(request.env["stock.lot"], "expiration_date"):
                        lot = request.env["stock.lot"].search(
                            lot_domain, order="expiration_date asc", limit=1
                        )
                        if lot and hasattr(lot, "expiration_date"):
                            fecha_vencimiento = lot.expiration_date
                    # Alternativa para Odoo 17: use_expiration_date
                    elif hasattr(request.env["stock.lot"], "use_expiration_date"):
                        lot = request.env["stock.lot"].search(
                            lot_domain, order="use_expiration_date asc", limit=1
                        )
                        if lot and hasattr(lot, "use_expiration_date"):
                            fecha_vencimiento = lot.use_expiration_date

                # Generar información de la línea de recepción
                linea_info = {
                    "id": move.id,
                    "id_move": move.id,
                    "id_recepcion": recepcion.id,
                    "product_id": product.id,
                    "product_name": product.display_name,
                    "product_code": product.default_code or "",
                    "product_barcode": product.barcode or "",
                    "product_tracking": product.tracking if hasattr(product, "tracking") else "",
                    "fecha_vencimiento": fecha_vencimiento or "",
                    "dias_vencimiento": (
                        product.expiration_time if hasattr(product, "expiration_time") else ""
                    ),
                    "other_barcodes": get_barcodes(product, move.id, recepcion.id),
                    "product_packing": array_packing,
                    "quantity_ordered": (
                        purchase_line.product_qty if purchase_line else move.product_uom_qty
                    ),  # Cambio para Odoo 17
                    "quantity_to_receive": move.product_uom_qty,  # Cambio para Odoo 17: product_qty -> product_uom_qty
                    "quantity_done": move.quantity_done,
                    "uom": (
                        move.product_uom_id.name if move.product_uom_id else "UND"
                    ),  # Cambio para Odoo 17: product_uom -> product_uom_id
                    "location_dest_id": move.location_dest_id.id or 0,
                    "location_dest_name": move.location_dest_id.display_name or "",
                    "location_dest_barcode": move.location_dest_id.barcode or "",
                    "location_id": move.location_id.id or 0,
                    "location_name": move.location_id.display_name or "",
                    "location_barcode": move.location_id.barcode or "",
                    "weight": product.weight or 0,
                    "detalle_lineas": [],
                }

                # Incluir detalles de las líneas (para trazabilidad)
                for move_line in move.move_line_ids:
                    lot = move_line.lot_id
                    location = move_line.location_id
                    location_dest = move_line.location_dest_id

                    expiration_date = ""
                    if lot:
                        if hasattr(lot, "expiration_date"):
                            expiration_date = lot.expiration_date
                        elif hasattr(lot, "use_expiration_date"):
                            expiration_date = lot.use_expiration_date

                    detalle_info = {
                        "id": move_line.id,
                        "qty_done": move_line.qty_done,
                        "qty_todo": move_line.product_uom_qty - move_line.qty_done,
                        "product_uom_qty": move_line.product_uom_qty,
                        "lot_id": lot.id if lot else 0,
                        "lot_name": lot and lot.name or "",
                        "expiration_date": expiration_date or "",
                        "location_id": location.id,
                        "location_name": location.name,
                        "location_barcode": location.barcode or "",
                        "location_dest_id": location_dest.id,
                        "location_dest_name": location_dest.name,
                        "location_dest_barcode": location_dest.barcode or "",
                        "package_id": move_line.package_id.id if move_line.package_id else 0,
                        "package_name": move_line.package_id.name if move_line.package_id else "",
                        "result_package_id": (
                            move_line.result_package_id.id if move_line.result_package_id else 0
                        ),
                        "result_package_name": (
                            move_line.result_package_id.name if move_line.result_package_id else ""
                        ),
                    }

                    linea_info["detalle_lineas"].append(detalle_info)

                recepcion_info["lineas_recepcion"].append(linea_info)

            return {"code": 200, "result": recepcion_info}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Asignar responsable a Recepcion
    @http.route("/api/asignar_responsable", auth="user", type="json", methods=["POST"], csrf=False)
    def asignar_responsable(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            id_responsable = auth.get("id_responsable", 0)

            # ✅ Validar ID de recepción
            if not id_recepcion:
                return {"code": 400, "msg": "ID de recepción no válido"}

            # ✅ Validar ID de responsable
            if not id_responsable:
                return {"code": 400, "msg": "ID de responsable no válido"}

            # ✅ Buscar recepción por ID
            recepcion = (
                request.env["stock.picking"]
                .sudo()
                .search([("id", "=", id_recepcion), ("picking_type_code", "=", "incoming")], limit=1)
            )

            # ✅ Validar recepción
            if not recepcion:
                return {"code": 400, "msg": "Recepción no encontrada"}

            # Validar si la recepcion ya tiene un responsable asignado
            if recepcion.responsable_id:
                return {"code": 400, "msg": "La recepción ya tiene un responsable asignado"}

            try:
                # ✅ Asignar responsable a la recepción
                # El código es igual en Odoo 17, pero agregamos manejo de errores adicional
                responsable_user = request.env["res.users"].sudo().browse(id_responsable)
                if not responsable_user.exists():
                    return {"code": 400, "msg": "El usuario responsable no existe"}

                data = recepcion.write({"responsable_id": id_responsable})

                if data:
                    return {"code": 200, "result": "Responsable asignado correctamente"}
                else:
                    return {"code": 400, "msg": "No se pudo asignar el responsable a la recepción"}

            except Exception as e:
                return {
                    "code": 400,
                    "msg": f"Error al asignar responsable a la recepción: {str(e)}",
                }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/asignar_responsable/batch", auth="user", type="json", methods=["POST"], csrf=False)
    def asignar_responsable_batch(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_batch = auth.get("id_batch", 0)
            id_responsable = auth.get("id_responsable", 0)

            # ✅ Validar ID de recepción
            if not id_batch:
                return {"code": 400, "msg": "ID del batch no válido"}

            # ✅ Validar ID de responsable
            if not id_responsable:
                return {"code": 400, "msg": "ID de responsable no válido"}

            # ✅ Buscar recepción por ID
            batch = request.env["stock.picking.batch"].sudo().search([("id", "=", id_batch)], limit=1)

            # ✅ Validar recepción
            if not batch:
                return {"code": 400, "msg": "Batch no encontrado"}

            # Validar si la batch ya tiene un responsable asignado
            if batch.user_id:
                return {"code": 400, "msg": "El batch ya tiene un responsable asignado"}

            try:
                # ✅ Asignar responsable a la recepción
                # El código es igual en Odoo 17, pero agregamos manejo de errores adicional
                responsable_user = request.env["res.users"].sudo().browse(id_responsable)
                if not responsable_user.exists():
                    return {"code": 400, "msg": "El usuario responsable no existe"}

                data = batch.write({"user_id": id_responsable})

                if data:
                    return {"code": 200, "result": "Responsable asignado correctamente"}
                else:
                    return {"code": 400, "msg": "No se pudo asignar el responsable al batch"}

            except Exception as e:
                return {"code": 400, "msg": f"Error al asignar responsable al batch: {str(e)}"}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/lotes/<int:id_producto>", auth="user", type="json", methods=["GET"])
    def get_lotes(self, id_producto):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            if not id_producto:
                return {"code": 400, "msg": "ID de producto no válido"}

            product = request.env["product.product"].sudo().search([("id", "=", id_producto)], limit=1)

            if not product:
                return {"code": 400, "msg": "Producto no encontrado"}

            if product.tracking != "lot":
                return {"code": 400, "msg": "El producto no tiene seguimiento por lotes"}

            # 🟡 Filtrar solo lotes que NO estén vencidos
            today = date.today()
            lotes = (
                request.env["stock.lot"]
                .sudo()
                .search(
                    [
                        ("product_id", "=", id_producto),
                        "|",
                        ("expiration_date", "=", False),
                        ("expiration_date", "!=", today),
                        # ("expiration_date", ">", today),
                    ]
                )
            )  # No tiene fecha de caducidad  # Fecha de caducidad futura

            array_lotes = []

            for lote in lotes:
                array_lotes.append(
                    {
                        "id": lote.id,
                        "name": lote.name or "",
                        "quantity": lote.product_qty or 0,
                        "expiration_date": lote.expiration_date or "",
                        "removal_date": lote.removal_date or "",
                        "use_date": lote.use_date or "",
                        "product_id": lote.product_id.id or 0,
                        "product_name": lote.product_id.name or "",
                    }
                )

            return {"code": 200, "result": array_lotes}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    # GET Obtener el lote con la fecha de vencimiento mas cercana
    @http.route("/api/lote_proximo_vencer/<int:id_producto>", auth="user", type="json", methods=["GET"])
    def get_lote_proximo_vencer(self, id_producto):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            if not id_producto:
                return {"code": 400, "msg": "ID de producto no válido"}

            product = request.env["product.product"].sudo().search([("id", "=", id_producto)], limit=1)
            if not product:
                product = (
                    request.env["product.product"]
                    .sudo()
                    .search([("default_code", "=", id_producto)], limit=1)
                )

            if not product:
                return {"code": 400, "msg": "Producto no encontrado"}

            if product.tracking != "lot":
                return {"code": 400, "msg": "El producto no tiene seguimiento por lotes"}

            if product.use_expiration_date is False:
                return {"code": 400, "msg": "El producto no maneja fecha de vencimiento"}

            today = date.today()
            lote = (
                request.env["stock.lot"]
                .sudo()
                .search(
                    [
                        ("product_id", "=", product.id),
                        ("expiration_date", "!=", False),
                        ("expiration_date", ">=", today),
                        # ("expiration_date", ">", today),
                    ],
                    order="expiration_date asc",
                    limit=1,
                )
            )

            if not lote:
                return {"code": 400, "msg": "No se encontraron lotes vigentes para el producto"}

            lote_info = {
                "id": lote.id,
                "name": lote.name or "",
                "quantity": lote.product_qty or 0,
                "expiration_date": lote.expiration_date or "",
                "removal_date": lote.removal_date or "",
                "use_date": lote.use_date or "",
                "product_id": lote.product_id.id or 0,
                "product_name": lote.product_id.name or "",
            }

            return {"code": 200, "result": lote_info}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/send_recepcion", auth="user", type="json", methods=["POST"], csrf=False)
    def send_recepcion(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            list_items = auth.get("list_items", [])

            recepcion = (
                request.env["stock.picking"]
                .sudo()
                .search(
                    [
                        ("id", "=", id_recepcion),
                        ("picking_type_code", "=", "incoming"),
                        ("state", "!=", "done"),
                    ],
                    limit=1,
                )
            )

            if not recepcion:
                return {
                    "code": 400,
                    "msg": f"Recepción no encontrada o ya completada con ID {id_recepcion}",
                }

            array_result = []

            # 🧠 Control para eliminar líneas automáticas solo una vez
            lineas_automaticas_borradas = False

            for item in list_items:
                move_id = item.get("id_move")
                product_id = item.get("id_producto")
                lote_id = item.get("lote_producto")
                ubicacion_destino = item.get("ubicacion_destino")
                cantidad = item.get("cantidad_separada")
                fecha_transaccion = item.get("fecha_transaccion")
                observacion = item.get("observacion")
                id_operario = item.get("id_operario")
                time_line = item.get("time_line")

                if not product_id or not cantidad:
                    continue

                product = request.env["product.product"].sudo().browse(product_id)
                if not product.exists():
                    continue

                move = (
                    request.env["stock.move"].sudo().browse(move_id)
                    if move_id
                    else recepcion.move_ids.filtered(lambda m: m.product_id.id == product_id)
                )
                if not move:
                    return {
                        "code": 400,
                        "msg": f"El producto {product.display_name} no está en la recepción",
                    }

                stock_move = move.sudo()

                lot = None
                if product.tracking == "lot":
                    if not lote_id:
                        return {
                            "code": 400,
                            "msg": f"El producto {product.display_name} requiere un lote",
                        }
                    lot = request.env["stock.lot"].sudo().browse(lote_id)
                    if not lot.exists():
                        return {
                            "code": 400,
                            "msg": f"Lote no encontrado para el producto {product.display_name}",
                        }

                # ✅ Eliminar líneas automáticas SOLO en la primera iteración
                if not lineas_automaticas_borradas:
                    lineas_auto = recepcion.move_line_ids.filtered(
                        lambda l: not l.user_operator_id and not l.is_done_item
                    )
                    lineas_auto.unlink()
                    lineas_automaticas_borradas = True  # ¡Ya está hecho!

                # ➕ Siempre crear una nueva línea con los datos del operario
                move_line_vals = {
                    "picking_id": recepcion.id,
                    "move_id": move.id,
                    "product_id": product.id,
                    "quantity": cantidad,
                    "location_id": move.location_id.id,
                    "location_dest_id": ubicacion_destino or move.location_dest_id.id,
                    "product_uom_id": move.product_uom.id,
                    "lot_id": lote_id if lote_id else False,
                    "date_transaction": (
                        procesar_fecha_naive(fecha_transaccion, "America/Bogota")
                        if fecha_transaccion
                        else datetime.now(pytz.utc)
                    ),
                    "new_observation": observacion,
                    "time": time_line,
                    "user_operator_id": id_operario,
                    "is_done_item": True,
                }

                move_line = request.env["stock.move.line"].sudo().create(move_line_vals)

                array_result.append(
                    {
                        "id": move_line.id,
                        "producto": product.display_name,
                        "cantidad": cantidad,
                        "lote": lot.name if lot else "",
                        "ubicacion_destino": ubicacion_destino,
                        "fecha_transaccion": fecha_transaccion,
                        "date_transaction": move_line.date_transaction,
                        "new_observation": move_line.new_observation,
                        "time": move_line.time,
                        "user_operator_id": move_line.user_operator_id.id,
                        "is_done_item": move_line.is_done_item,
                    }
                )

            stock_move.sudo().write({"picked": True})

            return {"code": 200, "result": array_result}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/update_recepcion", auth="user", type="json", methods=["POST"], csrf=False)
    def update_recepcion(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            list_items = auth.get("list_items", [])

            recepcion = (
                request.env["stock.picking"]
                .sudo()
                .search(
                    [
                        ("id", "=", id_recepcion),
                        ("picking_type_code", "=", "incoming"),
                        ("state", "!=", "done"),
                    ],
                    limit=1,
                )
            )

            if not recepcion:
                return {
                    "code": 400,
                    "msg": f"Recepción no encontrada o ya completada con ID {id_recepcion}",
                }

            array_result = []

            for item in list_items:
                move_line_id = item.get("id_move")

                # Buscar la línea de movimiento
                move_line = request.env["stock.move.line"].sudo().browse(move_line_id)

                if not move_line.exists():
                    array_result.append({"error": True, "mensaje": f"Línea {move_line_id} no encontrada"})
                    continue

                if move_line.picking_id.id != recepcion.id:
                    array_result.append(
                        {
                            "error": True,
                            "mensaje": f"Línea {move_line_id} no pertenece a esta recepción",
                        }
                    )
                    continue

                # Guardar información antes de eliminar para el resultado
                product_name = move_line.product_id.name
                move_id = move_line.move_id.id
                product_id = move_line.product_id.id
                cantidad = move_line.quantity

                # IMPORTANTE: Verificar otras líneas del mismo movimiento
                otras_lineas = recepcion.move_line_ids.filtered(
                    lambda l: l.move_id.id == move_id and l.id != move_line_id and l.is_done_item
                )

                # Registramos las líneas que deben seguir apareciendo
                move = move_line.move_id

                # Eliminar la línea
                move_line.sudo().unlink()

                array_result.append(
                    {
                        "error": False,
                        "mensaje": f"Línea {move_line_id} eliminada correctamente",
                        "producto": product_name,
                        "cantidad": cantidad,
                        "id_move": move_id,
                        "id_producto": product_id,
                        "id_move_deleted": move_line_id,
                    }
                )

            return {"code": 200, "result": array_result}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Enviar Temperatura de Recepción
    @http.route("/api/send_temperatura", auth="user", type="json", methods=["POST"], csrf=False)
    def send_temperatura(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id = auth.get("id", 0)
            temperatura = auth.get("temperatura", 0)

            recepcion = request.env["stock.picking"].sudo().search([("id", "=", id)], limit=1)

            if not recepcion:
                return {"code": 400, "msg": f"No se encontró registro con ID {id}"}

            recepcion.sudo().write({"temperature": temperatura})

            return {"code": 200, "result": "Temperatura registrada correctamente"}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Enviar Recepción por Batch
    @http.route("/api/send_recepcion/batch", auth="user", type="json", methods=["POST"], csrf=False)
    def send_recepcion_batch(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_batch = auth.get("id_batch", 0)
            list_items = auth.get("list_items", [])

            if not list_items:
                return {"code": 400, "msg": "No se recibieron líneas para procesar"}

            # ✅ Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            array_result = []

            for item in list_items:
                # Extraer datos del ítem
                move_id = item.get("id_move")
                product_id = item.get("id_producto")
                lote_id = item.get("lote_producto")
                ubicacion_destino = item.get("ubicacion_destino")
                cantidad = item.get("cantidad_separada")
                fecha_transaccion = item.get("fecha_transaccion")
                observacion = item.get("observacion", "")
                id_operario = item.get("id_operario")
                time_line = item.get("time_line")
                # Nuevo campo para controlar si se divide la línea
                # dividir = item.get("dividir", True)  # Por defecto True para mantener el comportamiento anterior

                dividir = item.get("dividir", False)  # Cambiado a False por defecto
                if observacion.lower() == "cantidad dividida":
                    dividir = True  # Si la observación es "producto en perfecto estado", no dividir

                # Validaciones básicas
                if not product_id:
                    array_result.append({"code": 400, "msg": f"Producto no especificado para algún ítem"})
                    continue

                product = request.env["product.product"].sudo().browse(product_id)
                if not product.exists():
                    array_result.append({"code": 400, "msg": f"El producto con ID {product_id} no existe"})
                    continue

                move_line = request.env["stock.move.line"].sudo().browse(move_id)
                if not move_line.exists():
                    array_result.append(
                        {"code": 400, "msg": f"La línea de movimiento con ID {move_id} no existe"}
                    )
                    continue

                # si viene el lote_id buscarlo y poner el nombre

                # Validar cantidad
                # if cantidad > move_line.quantity:
                #     array_result.append({"code": 400, "msg": f"La cantidad {cantidad} no puede ser mayor a la cantidad disponible {move_line.quantity} para el producto {product.display_name}"})
                #     continue

                # Preparar los datos comunes para actualización
                fecha_procesada = (
                    procesar_fecha_naive(fecha_transaccion, "America/Bogota")
                    if fecha_transaccion
                    else datetime.now(pytz.utc)
                )
                common_vals = {
                    "location_dest_id": ubicacion_destino,
                    "lot_name": lote_id,
                    "new_observation_packing": observacion,
                    "user_operator_id": id_operario,
                    "time_packing": time_line,
                    "date_transaction_packing": fecha_procesada,
                    "is_done_item_pack": True,
                }

                if lote_id:
                    lot = request.env["stock.lot"].sudo().browse(lote_id)
                    if not lot.exists():
                        array_result.append({"code": 400, "msg": f"El lote con ID {lote_id} no existe"})
                        continue
                    lote_id = lot.name
                else:
                    # eliminarlo de common_vals["lot_name"]
                    common_vals.pop("lot_name", None)

                if cantidad == 0:

                    # actualizar cantidad = 0  # No se actualiza la cantidad si es cero
                    common_vals["quantity"] = cantidad

                    # Actualizar la línea existente como procesada
                    move_line.write(common_vals)

                    # Referencia para el resultado
                    processed_line = move_line

                # CASO 2: Si la cantidad a procesar es menor que la cantidad en la línea Y el parámetro dividir es True,
                # dividimos la línea en dos: una procesada y una pendiente
                elif cantidad < move_line.quantity and dividir:
                    # Guardar la cantidad original antes de modificarla
                    cantidad_original = move_line.quantity

                    # 1. Actualizar línea original con la cantidad restante (no procesada)
                    move_line.write({"quantity": cantidad_original - cantidad})

                    # 2. Crear una nueva línea con la cantidad procesada
                    new_line_vals = move_line.copy_data()[0]
                    new_line_vals.update({"quantity": cantidad, **common_vals})

                    # 3. Crear la nueva línea procesada
                    new_line = request.env["stock.move.line"].sudo().create(new_line_vals)

                    # Referencia para el resultado
                    processed_line = new_line

                    # array_result.append({
                    #     "code": 200,
                    #     "msg": f"Línea dividida y procesada: {cantidad} de {cantidad_original}"
                    # })

                # CASO 3: Si la cantidad a procesar es exactamente igual a la cantidad de la línea O el parámetro dividir es False,
                # simplemente actualizamos la línea existente
                else:  # cantidad == move_line.quantity o dividir = False
                    # Si dividir es False y la cantidad es menor, simplemente actualizamos la cantidad
                    if not dividir and cantidad < move_line.quantity:
                        common_vals["quantity"] = cantidad

                    # Actualizar la línea existente como procesada
                    move_line.write(common_vals)

                    # Referencia para el resultado
                    processed_line = move_line

                    msg = f"Línea completa procesada: {cantidad}"
                    if not dividir and cantidad < move_line.quantity:
                        msg = f"Línea actualizada sin dividir: {cantidad} (dividir=False)"

                    # array_result.append({
                    #     "code": 200,
                    #     "msg": msg
                    # })

                # Agregar información detallada del procesamiento
                array_result.append(
                    {
                        "producto": product.display_name,
                        "cantidad": cantidad,
                        "lote": lote_id,
                        "ubicacion_destino": ubicacion_destino,
                        "fecha_transaccion": fecha_transaccion,
                        "date_transaction": processed_line.date_transaction_packing,
                        "new_observation_packing": processed_line.new_observation_packing,
                        "time": processed_line.time_packing,
                        "user_operator_id": (
                            processed_line.user_operator_id.id if processed_line.user_operator_id else None
                        ),
                        "is_done_item_pack": processed_line.is_done_item_pack,
                        "dividir": dividir,
                    }
                )

            return {"code": 200, "result": array_result}

        except Exception as e:
            import traceback

            return {
                "code": 500,
                "msg": f"Error interno: {str(e)}",
                "traceback": traceback.format_exc(),
            }

    @http.route("/api/send_image_linea_recepcion", auth="user", type="http", methods=["POST"], csrf=False)
    def send_image_linea_recepcion(self, **post):
        try:
            user = request.env.user
            if not user:
                return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

            show_photo_temperature = request.env["appwms.temperature"].sudo().search([], limit=1)
            show_photo_required = (
                show_photo_temperature.show_photo_temperature if show_photo_temperature else False
            )

            id_linea_recepcion = post.get("move_line_id")
            image_file = request.httprequest.files.get("image_data")
            temperatura = post.get("temperatura", 0.0)

            # Validar ID de línea de recepción
            if not id_linea_recepcion:
                return request.make_json_response({"code": 400, "msg": "ID de línea de recepción no válido"})

            # Validar archivo de imagen SOLO si show_photo_temperature es True
            if show_photo_required and not image_file:
                return request.make_json_response(
                    {"code": 400, "msg": "No se recibió ningún archivo de imagen"}
                )

            # Convertir ID a entero si viene como string
            try:
                id_linea_recepcion = int(id_linea_recepcion)
            except (ValueError, TypeError):
                return request.make_json_response(
                    {"code": 400, "msg": "ID de línea de recepción debe ser un número"}
                )

            # Buscar la línea de recepción por ID
            linea_recepcion = (
                request.env["stock.move.line"].sudo().search([("id", "=", id_linea_recepcion)], limit=1)
            )

            if not linea_recepcion:
                return request.make_json_response({"code": 404, "msg": "Línea de recepción no encontrada"})

            # Validar temperatura
            try:
                temperatura = float(temperatura)
            except (ValueError, TypeError):
                return request.make_json_response({"code": 400, "msg": "Temperatura debe ser un número"})

            # Variables para la respuesta
            image_data_base64 = None
            image_info = {}

            # Procesar imagen solo si existe
            if image_file:
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
                    "image_url": f"{base_url}/api/view_imagen_linea_recepcion/{id_linea_recepcion}",
                    "json_url": f"{base_url}/api/get_imagen_linea_recepcion/{id_linea_recepcion}",
                }

            # Actualizar la línea de recepción
            update_data = {"temperature": temperatura}
            if image_data_base64:
                update_data["imagen"] = image_data_base64

            linea_recepcion.sudo().write(update_data)

            # Preparar respuesta
            response_data = {
                "code": 200,
                "result": "Datos guardados correctamente",
                "line_id": id_linea_recepcion,
                "temperature": temperatura,
                "show_photo_temperature": show_photo_required,
                "product_name": (linea_recepcion.product_id.name if linea_recepcion.product_id else None),
                "image_processed": bool(image_file),
            }

            # Agregar información de imagen si se procesó
            if image_info:
                response_data.update(image_info)

            return request.make_json_response(response_data)

        except Exception as e:
            
            return request.make_json_response({"code": 500, "msg": "Error interno del servidor"})

    @http.route(
        "/api/view_imagen_linea_recepcion/<int:line_id>",
        auth="user",
        type="http",
        methods=["GET"],
        csrf=False,
    )
    def view_imagen_linea_recepcion(self, line_id, **kw):
        """
        Endpoint para visualizar la imagen de una línea de recepción (campo 'imagen')
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return request.make_response(
                    "Línea de recepción no encontrada",
                    status=404,
                    headers=[("Content-Type", "text/plain")],
                )

            # Verificar si tiene imagen
            if not linea_recepcion.imagen:
                return request.make_response(
                    "No hay imagen disponible para esta línea",
                    status=404,
                    headers=[("Content-Type", "text/plain")],
                )

            # Decodificar la imagen de base64
            try:
                image_data = base64.b64decode(linea_recepcion.imagen)
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
                    ("Content-Disposition", f"inline; filename=linea_recepcion_{line_id}.jpg"),
                ],
            )  # Cache por 1 hora

            return response

        except Exception as e:
            
            return request.make_response(
                "Error interno del servidor", status=500, headers=[("Content-Type", "text/plain")]
            )

    @http.route(
        "/api/get_imagen_linea_recepcion/<int:line_id>",
        auth="user",
        type="json",
        methods=["GET"],
        csrf=False,
    )
    def get_imagen_linea_recepcion_json(self, line_id, **kw):
        """
        Endpoint que devuelve la imagen de línea de recepción en formato JSON con base64
        Incluye también la temperatura si está disponible
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return {"code": 404, "msg": "Línea de recepción no encontrada"}

            # Verificar si tiene imagen
            if not linea_recepcion.imagen:
                return {"code": 404, "msg": "No hay imagen disponible para esta línea"}

            # Detectar tipo de imagen
            image_data = base64.b64decode(linea_recepcion.imagen)
            content_type = "image/jpeg"  # Por defecto

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

            return {
                "code": 200,
                "result": {
                    "line_id": line_id,
                    "image_base64": linea_recepcion.imagen,
                    "content_type": content_type,
                    "size": len(image_data),
                    "temperature": (
                        linea_recepcion.temperature if hasattr(linea_recepcion, "temperature") else None
                    ),
                    "move_id": linea_recepcion.move_id.id if linea_recepcion.move_id else None,
                    "product_name": (linea_recepcion.product_id.name if linea_recepcion.product_id else None),
                    "product_code": (
                        linea_recepcion.product_id.default_code if linea_recepcion.product_id else None
                    ),
                    "qty_done": linea_recepcion.qty_done,
                    "location_dest": (
                        linea_recepcion.location_dest_id.name if linea_recepcion.location_dest_id else None
                    ),
                },
            }

        except Exception as e:
            
            return {"code": 500, "msg": "Error interno del servidor"}

    @http.route(
        "/api/delete_imagen_linea_recepcion/<int:line_id>",
        auth="user",
        type="json",
        methods=["DELETE"],
        csrf=False,
    )
    def delete_imagen_linea_recepcion(self, line_id, **kw):
        """
        Endpoint para eliminar la imagen de una línea de recepción
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return {"code": 404, "msg": "Línea de recepción no encontrada"}

            # Verificar si tiene imagen
            if not linea_recepcion.imagen:
                return {"code": 404, "msg": "No hay imagen para eliminar"}

            # Eliminar la imagen (puedes decidir si también eliminar la temperatura)
            linea_recepcion.sudo().write(
                {
                    "imagen": False,
                    # "temperature": 0.0  # Descomenta si quieres resetear la temperatura también
                }
            )

            return {"code": 200, "result": "Imagen eliminada correctamente", "line_id": line_id}

        except Exception as e:
            
            return {"code": 500, "msg": "Error interno del servidor"}

    @http.route(
        "/api/update_imagen_linea_recepcion/<int:line_id>",
        auth="user",
        type="http",
        methods=["PUT"],
        csrf=False,
    )
    def update_imagen_linea_recepcion(self, line_id, **post):
        """
        Endpoint para actualizar solo la imagen de una línea de recepción existente
        """
        try:
            user = request.env.user
            if not user:
                return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

            image_file = request.httprequest.files.get("image_data")
            temperatura = post.get("temperatura")

            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return request.make_json_response({"code": 404, "msg": "Línea de recepción no encontrada"})

            # Validar archivo de imagen si se envía
            if image_file:
                # Validar tipo de archivo
                allowed_extensions = ["jpg", "jpeg", "png", "gif", "bmp", "webp"]
                file_extension = image_file.filename.lower().split(".")[-1] if image_file.filename else ""
                if file_extension not in allowed_extensions:
                    return request.make_json_response({"code": 400, "msg": "Formato de imagen no permitido"})

                # Leer el contenido del archivo y codificarlo a base64
                image_data_bytes = image_file.read()
                image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")
            else:
                image_data_base64 = linea_recepcion.imagen  # Mantener la imagen actual

            # Preparar datos para actualizar
            update_data = {"imagen": image_data_base64}

            # Actualizar temperatura si se proporciona
            if temperatura is not None:
                try:
                    update_data["temperature"] = float(temperatura)
                except (ValueError, TypeError):
                    return request.make_json_response({"code": 400, "msg": "Temperatura debe ser un número"})

            # Actualizar la línea de recepción
            linea_recepcion.sudo().write(update_data)

            return request.make_json_response(
                {
                    "code": 200,
                    "result": "Línea de recepción actualizada correctamente",
                    "line_id": line_id,
                    "image_updated": bool(image_file),
                    "temperature_updated": temperatura is not None,
                }
            )

        except Exception as e:
            
            return request.make_json_response({"code": 500, "msg": "Error interno del servidor"})

    @http.route("/api/send_imagen_observation", auth="user", type="http", methods=["POST"], csrf=False)
    def send_imagen_observation(self, **post):
        try:
            user = request.env.user
            if not user:
                return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

            id_move = post.get("id_move")
            image_file = request.httprequest.files.get("image_data")

            # Validar ID de stock move
            if not id_move:
                return request.make_json_response({"code": 400, "msg": "ID de stock move no válido"})

            # Validar archivo de imagen
            if not image_file:
                return request.make_json_response(
                    {"code": 400, "msg": "No se recibió ningún archivo de imagen"}
                )

            # Convertir ID a entero si viene como string
            try:
                id_move = int(id_move)
            except (ValueError, TypeError):
                return request.make_json_response({"code": 400, "msg": "ID de stock move debe ser un número"})

            # Buscar el stock move por ID
            stock_move = request.env["stock.move"].sudo().search([("id", "=", id_move)], limit=1)

            if not stock_move:
                return request.make_json_response({"code": 404, "msg": "Stock move no encontrado"})

            # Obtener la última línea de stock move relacionada al stock move
            # Ordenamos por ID descendente para obtener la más reciente
            linea_recepcion = (
                request.env["stock.move.line"]
                .sudo()
                .search([("move_id", "=", stock_move.id)], order="id desc", limit=1)
            )

            if not linea_recepcion:
                return request.make_json_response(
                    {
                        "code": 404,
                        "msg": "No se encontraron líneas de recepción para este stock move",
                    }
                )

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

            # Validar tamaño del archivo (opcional - ejemplo: máximo 5MB)
            max_size = 5 * 1024 * 1024  # 5MB en bytes
            image_file.seek(0, 2)  # Ir al final del archivo
            file_size = image_file.tell()
            image_file.seek(0)  # Volver al inicio

            if file_size > max_size:
                return request.make_json_response(
                    {"code": 400, "msg": "El archivo es demasiado grande. Tamaño máximo: 5MB"}
                )

            # Leer el contenido del archivo y codificarlo a base64
            image_data_bytes = image_file.read()
            image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

            # Guardar la imagen codificada en base64 en la línea de recepción
            linea_recepcion.sudo().write({"imagen_observation": image_data_base64})

            # Generar la URL para ver la imagen (si tienes un endpoint para visualizar)
            base_url = request.httprequest.host_url.rstrip("/")
            image_url = f"{base_url}/api/view_imagen_observation/{linea_recepcion.id}"

            return request.make_json_response(
                {
                    "code": 200,
                    "result": "Imagen de observación guardada correctamente",
                    "stock_move_id": stock_move.id,
                    "stock_move_line_id": linea_recepcion.id,
                    "image_url": image_url,
                    "filename": image_file.filename,
                }
            )

        except Exception as e:
            
            return request.make_json_response({"code": 500, "msg": f"Error interno del servidor"})

    @http.route(
        "/api/view_imagen_observation/<int:line_id>",
        auth="user",
        type="http",
        methods=["GET"],
        csrf=False,
    )
    def view_imagen_observation(self, line_id, **kw):
        """
        Endpoint para visualizar la imagen de observación de una línea de recepción
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return request.make_response(
                    "Línea de recepción no encontrada",
                    status=404,
                    headers=[("Content-Type", "text/plain")],
                )

            # Verificar si tiene imagen
            if not linea_recepcion.imagen_observation:
                return request.make_response(
                    "No hay imagen disponible para esta línea",
                    status=404,
                    headers=[("Content-Type", "text/plain")],
                )

            # Decodificar la imagen de base64
            try:
                image_data = base64.b64decode(linea_recepcion.imagen_observation)
            except Exception as e:
                
                return request.make_response(
                    "Error al procesar la imagen",
                    status=500,
                    headers=[("Content-Type", "text/plain")],
                )

            # Detectar el tipo de contenido de la imagen
            # Puedes usar python-magic si está disponible, o detectar por los primeros bytes
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
                    ("Content-Disposition", f"inline; filename=observation_{line_id}.jpg"),
                ],
            )  # Cache por 1 hora

            return response

        except Exception as e:
            
            return request.make_response(
                "Error interno del servidor", status=500, headers=[("Content-Type", "text/plain")]
            )

    @http.route(
        "/api/get_imagen_observation/<int:line_id>",
        auth="user",
        type="json",
        methods=["GET"],
        csrf=False,
    )
    def get_imagen_observation_json(self, line_id, **kw):
        """
        Endpoint alternativo que devuelve la imagen en formato JSON con base64
        Útil para aplicaciones que prefieren trabajar con JSON
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return {"code": 404, "msg": "Línea de recepción no encontrada"}

            # Verificar si tiene imagen
            if not linea_recepcion.imagen_observation:
                return {"code": 404, "msg": "No hay imagen disponible para esta línea"}

            # Detectar tipo de imagen
            image_data = base64.b64decode(linea_recepcion.imagen_observation)
            content_type = "image/jpeg"  # Por defecto

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

            return {
                "code": 200,
                "result": {
                    "line_id": line_id,
                    "image_base64": linea_recepcion.imagen_observation,
                    "content_type": content_type,
                    "size": len(image_data),
                    "move_id": linea_recepcion.move_id.id if linea_recepcion.move_id else None,
                    "product_name": (linea_recepcion.product_id.name if linea_recepcion.product_id else None),
                },
            }

        except Exception as e:
            
            return {"code": 500, "msg": "Error interno del servidor"}

    @http.route(
        "/api/delete_imagen_observation/<int:line_id>",
        auth="user",
        type="json",
        methods=["DELETE"],
        csrf=False,
    )
    def delete_imagen_observation(self, line_id, **kw):
        """
        Endpoint para eliminar la imagen de observación de una línea de recepción
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return {"code": 404, "msg": "Línea de recepción no encontrada"}

            # Verificar si tiene imagen
            if not linea_recepcion.imagen_observation:
                return {"code": 404, "msg": "No hay imagen para eliminar"}

            # Eliminar la imagen
            linea_recepcion.sudo().write({"imagen_observation": False})

            return {"code": 200, "result": "Imagen eliminada correctamente", "line_id": line_id}

        except Exception as e:
            
            return {"code": 500, "msg": "Error interno del servidor"}

    ## GET Obtener todas las ubicaciones
    @http.route("/api/ubicaciones", auth="user", type="json", methods=["GET"])
    def get_ubicaciones(self):
        try:
            user = request.env.user
            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            array_ubicaciones = []

            for warehouse in allowed_warehouses:
                # ✅ Obtener todas las ubicaciones
                ubicaciones = (
                    request.env["stock.location"]
                    .sudo()
                    .search(
                        [
                            ("usage", "=", "internal"),
                            ("active", "=", True),
                            ("warehouse_id", "=", warehouse.id),
                        ]
                    )
                )

                for ubicacion in ubicaciones:
                    array_ubicaciones.append(
                        {
                            "id": ubicacion.id,
                            "name": ubicacion.display_name,
                            "barcode": ubicacion.barcode or "",
                            "location_id": ubicacion.location_id.id if ubicacion.location_id else 0,
                            "location_name": (
                                ubicacion.location_id.display_name if ubicacion.location_id else ""
                            ),
                            "id_warehouse": warehouse.id,
                            "warehouse_name": warehouse.name,
                        }
                    )

            return {"code": 200, "result": array_ubicaciones}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Completar Recepcion
    @http.route("/api/complete_recepcion", auth="user", type="json", methods=["POST"], csrf=False)
    def complete_recepcion(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            crear_backorder = auth.get(
                "crear_backorder", True
            )  # Parámetro para controlar la creación de backorder

            # ✅ Buscar recepción por ID
            recepcion = (
                request.env["stock.picking"]
                .sudo()
                .search(
                    [
                        ("id", "=", id_recepcion),
                        ("picking_type_code", "=", "incoming"),
                        ("state", "!=", "done"),
                    ],
                    limit=1,
                )
            )

            if not recepcion:
                return {
                    "code": 400,
                    "msg": f"Recepción no encontrada o ya completada con ID {id_recepcion}",
                }

            # Intentar validar la recepción
            result = recepcion.sudo().button_validate()

            # Si el resultado es un diccionario, significa que se requiere acción adicional (un wizard)
            if isinstance(result, dict) and result.get("res_model"):
                wizard_model = result.get("res_model")

                # Para asistente de backorder
                if wizard_model == "stock.backorder.confirmation":
                    # Crear el wizard con los valores del contexto
                    wizard_context = result.get("context", {})

                    # Crear el asistente con los valores correctos según tu JSON
                    # En Odoo 17, la forma de enlazar registros sigue siendo la misma
                    wizard_vals = {
                        "pick_ids": [(4, id_recepcion)],
                        "show_transfers": wizard_context.get("default_show_transfers", False),
                    }

                    wizard = (
                        request.env[wizard_model].sudo().with_context(**wizard_context).create(wizard_vals)
                    )

                    # Procesar según la opción de crear_backorder
                    if crear_backorder:
                        # En Odoo 17, el método process sigue existiendo
                        wizard.sudo().process()
                        return {
                            "code": 200,
                            "msg": f"Recepción parcial completada y backorder creado - ID {wizard.id or 0}",
                        }
                    else:
                        # En Odoo 17, el método process_cancel_backorder sigue existiendo
                        wizard.sudo().process_cancel_backorder()
                        return {
                            "code": 200,
                            "msg": "Recepción parcial completada sin crear backorder",
                        }

                # Para asistente de transferencia inmediata
                elif wizard_model == "stock.immediate.transfer":
                    wizard_context = result.get("context", {})
                    wizard = (
                        request.env[wizard_model]
                        .sudo()
                        .with_context(**wizard_context)
                        .create({"pick_ids": [(4, id_recepcion)]})
                    )

                    wizard.sudo().process()
                    return {"code": 200, "msg": "Recepción procesada con transferencia inmediata"}

                else:
                    return {
                        "code": 400,
                        "msg": f"Se requiere un asistente no soportado: {wizard_model}",
                    }

            # Si llegamos aquí, button_validate completó la validación sin necesidad de asistentes
            return {"code": 200, "msg": "Recepción completada correctamente"}

        except Exception as e:
            # Registrar el error completo para depuración
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Crear Lote
    @http.route("/api/create_lote", auth="user", type="json", methods=["POST"], csrf=False)
    def create_lote(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_producto = auth.get("id_producto", 0)
            nombre_lote = auth.get("nombre_lote", "")
            fecha_vencimiento = auth.get("fecha_vencimiento", "")

            # ✅ Validar ID de producto
            if not id_producto:
                return {"code": 400, "msg": "ID de producto no válido"}

            # ✅ Validar nombre de lote
            if not nombre_lote:
                return {"code": 400, "msg": "Nombre de lote no válido"}

            # ✅ Buscar producto por ID
            product = request.env["product.product"].sudo().search([("id", "=", id_producto)], limit=1)

            # ✅ Validar producto
            if not product:
                return {"code": 400, "msg": "Producto no encontrado"}

            # validar que el lote con ese nombre para ese producto no exista
            lot = (
                request.env["stock.lot"]
                .sudo()
                .search([("name", "=", nombre_lote), ("product_id", "=", id_producto)], limit=1)
            )
            if lot:
                return {"code": 400, "msg": "El lote ya existe para este producto"}

            # ✅ Convertir fecha de string a datetime object
            expiration_datetime = None
            if fecha_vencimiento:
                try:
                    # Limpiar espacios en blanco
                    fecha_vencimiento = fecha_vencimiento.strip()

                    # Intentar diferentes formatos de fecha
                    formatos_fecha = [
                        "%Y-%m-%d %H:%M:%S",  # 2025-08-05 12:30:00
                        "%Y-%m-%d %H:%M",  # 2025-08-05 12:00
                        "%Y-%m-%d",  # 2025-08-05
                        "%d/%m/%Y %H:%M:%S",  # 05/08/2025 12:30:00
                        "%d/%m/%Y %H:%M",  # 05/08/2025 12:00
                        "%d/%m/%Y",  # 05/08/2025
                        "%d-%m-%Y",  # 05-08-2025
                        "%Y/%m/%d",  # 2025/08/05
                    ]

                    for formato in formatos_fecha:
                        try:
                            expiration_datetime = datetime.strptime(fecha_vencimiento, formato)
                            break
                        except ValueError:
                            continue

                    if not expiration_datetime:
                        return {
                            "code": 400,
                            "msg": f"Formato de fecha inválido: '{fecha_vencimiento}'. Use YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, DD/MM/YYYY, etc.",
                        }

                    # ✅ Validar que la fecha de vencimiento sea mayor a la fecha actual
                    if expiration_datetime <= datetime.now():
                        return {
                            "code": 400,
                            "msg": "La fecha de vencimiento debe ser mayor a la fecha actual",
                        }

                except Exception as e:
                    return {"code": 400, "msg": f"Error procesando fecha: {str(e)}"}

            if expiration_datetime and product.tracking == "lot" and product.use_expiration_date:
                today = date.today()
                lote_proximo = (
                    request.env["stock.lot"]
                    .sudo()
                    .search(
                        [
                            ("product_id", "=", product.id),
                            ("expiration_date", "!=", False),
                            ("expiration_date", ">=", today),
                        ],
                        order="expiration_date asc",
                        limit=1,
                    )
                )

                if lote_proximo and lote_proximo.expiration_date:
                    fecha_lote_proximo = lote_proximo.expiration_date
                    # Convertir ambas fechas a date para comparar solo día/mes/año
                    if expiration_datetime.date() < fecha_lote_proximo.date():
                        return {
                            "code": 400,
                            "msg": f"{product.name} La fecha de vencimiento asignada ({expiration_datetime.date()}) no puede ser más corta a la del lote más próximo a vencer {lote_proximo.name} - ({fecha_lote_proximo.date()}) ya registrado para este producto. Revise las fechas e intente nuevamente.",
                        }

            # ✅ Crear lote
            lot_data = {
                "name": nombre_lote,
                "product_id": product.id,
                "company_id": product.company_id.id or user.company_id.id,
            }

            # Solo agregar fechas si se proporcionó fecha_vencimiento
            if expiration_datetime:
                lot_data.update(
                    {
                        "expiration_date": expiration_datetime,
                        "removal_date": expiration_datetime,
                        "use_date": expiration_datetime,
                    }
                )

            lot = request.env["stock.lot"].sudo().create(lot_data)

            response = {
                "id": lot.id,
                "name": lot.name,
                "quantity": lot.product_qty,
                "expiration_date": lot.expiration_date.isoformat() if lot.expiration_date else "",
                "removal_date": lot.removal_date.isoformat() if lot.removal_date else "",
                "use_date": lot.use_date.isoformat() if lot.use_date else "",
                "product_id": lot.product_id.id,
                "product_name": lot.product_id.name,
            }

            return {"code": 200, "result": response}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Actualizar Lote
    @http.route("/api/update_lote", auth="user", type="json", methods=["POST"], csrf=False)
    def update_lote(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_lote = auth.get("id_lote", 0)
            nombre_lote = auth.get("nombre_lote", "")
            fecha_vencimiento = auth.get("fecha_vencimiento", "")

            # ✅ Validar ID de lote
            if not id_lote:
                return {"code": 400, "msg": "ID de lote no válido"}

            # ✅ Validar nombre de lote
            if not nombre_lote:
                return {"code": 400, "msg": "Nombre de lote no válido"}

            # ✅ Buscar lote por ID
            # En Odoo 17, stock.production.lot cambió a stock.lot
            lot = request.env["stock.lot"].sudo().search([("id", "=", id_lote)], limit=1)

            # ✅ Validar lote
            if not lot:
                return {"code": 400, "msg": "Lote no encontrado"}

            # ✅ Actualizar lote
            lot.sudo().write(
                {
                    "name": nombre_lote,
                    "expiration_date": fecha_vencimiento,
                    # El campo alert_date ya no existe en Odoo 17
                    "use_date": fecha_vencimiento,
                    "removal_date": fecha_vencimiento,
                    "alert_date": fecha_vencimiento,
                }
            )

            response = {
                "id": lot.id,
                "name": lot.name,
                "quantity": lot.product_qty,
                "expiration_date": lot.expiration_date,
                # Cambio de alert_date a removal_date en la respuesta
                "use_date": lot.use_date,
                "removal_date": lot.removal_date,
                "alert_date": lot.removal_date,
                "product_id": lot.product_id.id,
                "product_name": lot.product_id.name,
            }

            return {"code": 200, "result": response}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## comprobar disponibilidad de la recepcion
    @http.route("/api/check_availability", auth="user", type="json", methods=["POST"], csrf=False)
    def check_availability(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            recepcion = (
                request.env["stock.picking"]
                .sudo()
                .search(
                    [
                        ("id", "=", id_recepcion),
                        ("picking_type_code", "=", "incoming"),
                        ("state", "!=", "done"),
                    ],
                    limit=1,
                )
            )

            if not recepcion:
                return {
                    "code": 400,
                    "msg": f"Recepción no encontrada o ya completada con ID {id_recepcion}",
                }

            # Obtener si se maneja Crear orden parcial
            create_backorder = (
                recepcion.picking_type_id.create_backorder
                if hasattr(recepcion.picking_type_id, "create_backorder")
                else False
            )

            # ✅ Ejecutar comprobación de disponibilidad
            recepcion.action_assign()

            movimientos_pendientes = recepcion.move_ids.filtered(
                lambda m: m.state in ["confirmed", "assigned"]
            )
            if not movimientos_pendientes:
                return {"code": 200, "msg": "No hay líneas pendientes", "result": {}}

            purchase_order = recepcion.purchase_id or (
                recepcion.origin
                and request.env["purchase.order"].sudo().search([("name", "=", recepcion.origin)], limit=1)
            )
            peso_total = sum(
                move.product_id.weight * move.product_uom_qty
                for move in movimientos_pendientes
                if move.product_id.weight
            )
            numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

            recepcion_info = {
                "id": recepcion.id,
                "name": recepcion.name,
                "fecha_creacion": recepcion.create_date,
                "proveedor_id": recepcion.partner_id.id,
                "proveedor": recepcion.partner_id.name,
                "location_dest_id": recepcion.location_dest_id.id,
                "location_dest_name": recepcion.location_dest_id.display_name,
                "purchase_order_id": purchase_order.id if purchase_order else 0,
                "purchase_order_name": purchase_order.name if purchase_order else "",
                "numero_entrada": recepcion.name,
                "peso_total": peso_total,
                "numero_lineas": 0,
                "numero_items": 0,
                "state": recepcion.state,
                "create_backorder": create_backorder,
                "origin": recepcion.origin or "",
                "priority": recepcion.priority,
                "warehouse_id": recepcion.picking_type_id.warehouse_id.id,
                "warehouse_name": recepcion.picking_type_id.warehouse_id.name,
                "location_id": recepcion.location_id.id,
                "location_name": recepcion.location_id.display_name,
                # "responsable_id": recepcion.user_id.id if recepcion.user_id else 0,
                # "responsable": recepcion.user_id.name if recepcion.user_id else "",
                "responsable_id": recepcion.responsable_id.id if recepcion.responsable_id else 0,
                "responsable": recepcion.responsable_id.name if recepcion.responsable_id else "",
                "picking_type": recepcion.picking_type_id.name,
                "backorder_id": recepcion.backorder_id.id if recepcion.backorder_id else 0,
                "backorder_name": recepcion.backorder_id.name if recepcion.backorder_id else "",
                "start_time_reception": recepcion.start_time_reception or "",
                "end_time_reception": recepcion.end_time_reception or "",
                "picking_type_code": recepcion.picking_type_code,
                "show_check_availability": getattr(recepcion, "show_check_availability", False),
                "lineas_recepcion": [],
                "lineas_recepcion_enviadas": [],
            }

            for move in movimientos_pendientes:
                product = move.product_id
                purchase_line = move.purchase_line_id
                cantidad_faltante = move.product_uom_qty - sum(
                    l.quantity for l in move.move_line_ids if l.is_done_item
                )

                if cantidad_faltante <= 0:
                    continue

                array_barcodes = (
                    [{"barcode": b.name} for b in product.barcode_ids]
                    if hasattr(product, "barcode_ids")
                    else []
                )
                array_packing = (
                    [
                        {
                            "barcode": p.barcode,
                            "cantidad": p.qty,
                            "id_move": p.id,
                            "id_product": p.product_id.id,
                            "batch_id": recepcion.id,
                        }
                        for p in product.packaging_ids
                    ]
                    if hasattr(product, "packaging_ids")
                    else []
                )

                fecha_vencimiento = ""
                if product.tracking == "lot":
                    lot = request.env["stock.lot"].search(
                        [("product_id", "=", product.id)], order="expiration_date asc", limit=1
                    )
                    fecha_vencimiento = lot.expiration_date if lot and hasattr(lot, "expiration_date") else ""

                linea_info = {
                    "id": move.id,
                    "id_move": move.id,
                    "id_recepcion": recepcion.id,
                    "state": move.state,
                    "product_id": product.id,
                    "product_name": product.display_name,
                    "product_code": product.default_code or "",
                    "product_barcode": product.barcode or "",
                    "product_tracking": product.tracking or "",
                    "fecha_vencimiento": fecha_vencimiento or "",
                    "dias_vencimiento": (
                        product.expiration_time if hasattr(product, "expiration_time") else ""
                    ),
                    "other_barcodes": get_barcodes(product, move.id, recepcion.id),
                    "product_packing": array_packing,
                    "quantity_ordered": (
                        purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                    ),
                    "quantity_to_receive": move.product_uom_qty,
                    "quantity_done": move.quantity,
                    "uom": move.product_uom.name if move.product_uom else "UND",
                    "location_dest_id": move.location_dest_id.id or 0,
                    "location_dest_name": move.location_dest_id.display_name or "",
                    "location_dest_barcode": move.location_dest_id.barcode or "",
                    "location_id": move.location_id.id or 0,
                    "location_name": move.location_id.display_name or "",
                    "location_barcode": move.location_id.barcode or "",
                    "weight": product.weight or 0,
                    "cantidad_faltante": cantidad_faltante,
                }

                recepcion_info["lineas_recepcion"].append(linea_info)

                for move_line in move.move_line_ids.filtered(lambda ml: ml.is_done_item):
                    cantidad_faltante = move_line.quantity - sum(
                        l.quantity for l in move_line.move_line_ids if l.is_done_item
                    )

                    linea_enviada_info = {
                        "id": move_line.id,
                        "id_move_line": move_line.id,
                        "id_move": move_line.id,
                        # "id_move": move.id,
                        "id_recepcion": recepcion.id,
                        "product_id": product.id,
                        "product_name": product.display_name,
                        "product_code": product.default_code or "",
                        "product_barcode": product.barcode or "",
                        "product_tracking": product.tracking or "",
                        "quantity_ordered": (
                            purchase_line.product_uom_qty if purchase_line else move.product_uom_qty
                        ),
                        "quantity_to_receive": move.product_uom_qty,
                        "quantity_done": move_line.quantity,
                        "cantidad_faltante": cantidad_faltante,
                        "uom": move_line.product_uom_id.name if move_line.product_uom_id else "UND",
                        "location_dest_id": move_line.location_dest_id.id or 0,
                        "location_dest_name": move_line.location_dest_id.display_name or "",
                        "location_dest_barcode": move_line.location_dest_id.barcode or "",
                        "location_id": move_line.location_id.id or 0,
                        "location_name": move_line.location_id.display_name or "",
                        "location_barcode": move_line.location_id.barcode or "",
                        "is_done_item": move_line.is_done_item,
                        "date_transaction": getattr(move_line, "date_transaction", ""),
                        "observation": getattr(move_line, "new_observation", ""),
                        "time": getattr(move_line, "time", 0),
                        "user_operator_id": (
                            move_line.user_operator_id.id if move_line.user_operator_id else 0
                        ),
                    }

                    if move_line.lot_id:
                        linea_enviada_info.update(
                            {
                                "lot_id": move_line.lot_id.id,
                                "lot_name": move_line.lot_id.name,
                                "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                            }
                        )
                    elif move_line.lot_name:
                        linea_enviada_info.update(
                            {
                                "lot_id": 0,
                                "lot_name": move_line.lot_name,
                                "fecha_vencimiento": "",
                            }
                        )

                    recepcion_info["lineas_recepcion_enviadas"].append(linea_enviada_info)

            recepcion_info["numero_lineas"] = len(recepcion_info["lineas_recepcion"])
            recepcion_info["numero_items"] = sum(
                linea["quantity_to_receive"] for linea in recepcion_info["lineas_recepcion"]
            )

            return {
                "code": 200,
                "msg": "Disponibilidad comprobada correctamente",
                "result": recepcion_info,
            }

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}


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


def obtener_info_ubicacion(ubicacion_id):
    ubicacion = request.env["stock.location"].sudo().browse(ubicacion_id)

    if not ubicacion.exists():
        return {"code": 400, "msg": f"La ubicación con ID {ubicacion_id} no existe"}

    return ubicacion


def obtener_info_producto(product_id):
    producto = request.env["product.product"].sudo().browse(product_id)

    if not producto.exists():
        return {"code": 400, "msg": f"El producto con ID {product_id} no existe"}

    return producto


def validate_pda(device_id):
    """
    Solo valida que la PDA existe y está autorizada
    Returns: dict con error si hay problema, None si todo está OK
    """
    if not device_id:
        return {
            "code": 400,
            "msg": "Device ID no proporcionado, por favor actualizar a la ultima version de la app",
        }

    pda = request.env["pda.logs"].sudo().search([("device_id", "=", device_id)])

    if not pda:
        return {"code": 404, "msg": "PDA no encontrado"}

    if pda.is_authorized == "no":
        return {"code": 403, "msg": "PDA no autorizado"}

    # Si llegamos aquí, todo está bien
    return None
