# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, timedelta
import pytz
import hashlib
import json
from datetime import datetime, timedelta
from collections import defaultdict


# Cache global simple
_cache = {}
_cache_timestamps = {}
CACHE_TTL = 300  # 5 minutos


def generate_cache_key(user_id, params=None):
    """Generar clave de caché única"""
    cache_data = {"user_id": user_id}
    if params:
        cache_data.update(params)
    cache_string = json.dumps(cache_data, sort_keys=True)
    return hashlib.md5(cache_string.encode()).hexdigest()


def get_from_cache(cache_key):
    """Obtener datos del cache si están válidos"""
    if cache_key in _cache and cache_key in _cache_timestamps:
        cache_age = datetime.now() - _cache_timestamps[cache_key]
        if cache_age < timedelta(seconds=CACHE_TTL):
            return _cache[cache_key]
    return None


def set_cache(cache_key, data):
    """Almacenar datos en cache"""
    _cache[cache_key] = data
    _cache_timestamps[cache_key] = datetime.now()


class TransaccionDataPicking(http.Controller):

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

    @http.route("/api/batchs", auth="user", type="json", methods=["GET"])
    def get_batches(self, **kwargs):
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
                return {"code": 400, "update_version": update_required, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            # obtener la configuracion picking de la app
            config_picking = request.env["picking.config.general"].sudo().browse(1)

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            all_location_ids = list(
                {
                    loc_id
                    for zone in user_wms.zone_ids.sudo().read(["location_ids"])
                    for loc_id in zone["location_ids"]
                }
            )

            if not all_location_ids:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "El usuario no tiene ubicaciones asociadas",
                }

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(
                    request.env["stock.location"]
                    .sudo()
                    .browse(chunk)
                    .read(
                        [
                            "id",
                            "name",
                            "complete_name",
                            "priority_picking",
                            "barcode",
                            "priority_picking_desplay",
                        ]
                    )
                )

            user_location_ids = [location["id"] for location in locations]

            search_domain = [
                ("state", "=", "in_progress"),
                ("picking_type_code", "=", "internal"),
                ("picking_type_id.sequence_code", "!=", "PC"),
            ]

            # ✅ Filtrar por responsable si config_picking es 'responsible'
            if config_picking.picking_type == "responsible":
                search_domain.append(("user_id", "=", user.id))  # Agregar filtro por usuario responsable

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search(search_domain)

            # ✅ Verificar si no hay lotes encontrados
            if not batchs:
                return {"code": 200, "msg": "No tienes batches asignados"}

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos unificados
                move_unified_ids = (
                    request.env["move.line.unified"]
                    .sudo()
                    .search(
                        [("stock_picking_batch_id", "=", batch.id), ("location_id", "in", user_location_ids)]
                    )
                )

                if not move_unified_ids:
                    continue

                # ✅ NUEVO: Verificar si todos los items están completados
                total_items = len(move_unified_ids)
                completed_items = len(move_unified_ids.filtered(lambda m: m.is_done_item))

                # Si todos los items están completados, saltar este batch
                if total_items > 0 and completed_items == total_items:
                    continue

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
                origin_details = origins_list if origins_list else []

                # stock_moves = move_unified_ids.read(["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty", "is_done_item"])
                stock_moves = move_unified_ids.read()

                array_batch_temp = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "rol": user_wms.user_rol or "USER",
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "scheduleddate": batch.scheduled_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "muelle": batch.location_id.display_name if batch.location_id else "SIN-MUELLE",
                    "id_muelle": batch.location_id.id if batch.location_id else "",
                    "id_muelle_padre": batch.location_id.location_id.id if batch.location_id else "",
                    "barcode_muelle": batch.location_id.barcode or "",
                    "count_items": len(stock_moves),
                    "total_quantity_items": sum(move["product_uom_qty"] for move in stock_moves),
                    # ✅ NUEVO: Agregar información de progreso
                    "completed_items": completed_items,
                    "progress_percentage": (
                        round((completed_items / total_items) * 100, 2) if total_items > 0 else 0
                    ),
                    "start_time_pick": batch.start_time_pick or "",
                    "end_time_pick": batch.end_time_pick or "",
                    "zona_entrega": (
                        batch.picking_ids[0].delivery_zone_id.name
                        if batch.picking_ids and batch.picking_ids[0].delivery_zone_id
                        else "SIN-ZONA"
                    ),
                    "origin": origin_details,
                    "list_items": [],
                }

                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {
                    prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)
                }

                location_ids = {move["location_id"][0] for move in stock_moves}
                locations_dict = {
                    loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)
                }

                for move in stock_moves:
                    product = products.get(move["product_id"][0])
                    location = locations_dict.get(move["location_id"][0])
                    location_dest = locations_dict.get(move["location_dest_id"][0])

                    # ✅ Obtener códigos de barras adicionales
                    array_all_barcode = (
                        [
                            {
                                "barcode": barcode.name,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": [
                                    move["product_id"][0] if move["product_id"] else 0,
                                    move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                                ],
                                "cantidad": 1,
                                "id_product": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for barcode in product.barcode_ids
                            if barcode.name  # Filtra solo los barcodes válidos
                        ]
                        if product.barcode_ids
                        else []
                    )

                    # ✅ Obtener empaques del producto
                    array_packing = (
                        [
                            {
                                "barcode": pack.barcode,
                                "cantidad": pack.qty,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for pack in product.packaging_ids
                            if pack.barcode
                        ]
                        if product.packaging_ids
                        else []
                    )

                    # ✅ Buscar el picking_id desde stock.move
                    picking = (
                        request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)
                    )  # Obtiene un picking asociado al batch
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener el nombre del pedido
                    picking_name = picking.display_name if picking else ""

                    # ✅ Obtener la zona de entrega del picking
                    delivery_zone_name = (
                        picking.delivery_zone_id.display_name
                        if picking and picking.delivery_zone_id
                        else "SIN-ZONA"
                    )
                    delivery_zone_id = (
                        picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0
                    )

                    user_operator_id = 0
                    user_operator_name = ""
                    if (
                        move.get("user_operator_id")
                        and isinstance(move["user_operator_id"], (list, tuple))
                        and len(move["user_operator_id"]) > 0
                    ):
                        user_operator_id = move["user_operator_id"][0]
                        user_operator_name = (
                            move["user_operator_id"][1] if len(move["user_operator_id"]) > 1 else ""
                        )

                    array_batch_temp["list_items"].append(
                        {
                            "batch_id": batch.id,
                            "id_move": move["id"],
                            "picking_id": picking_id,
                            "id_product": move["product_id"][0] if move["product_id"] else 0,
                            "product_id": [
                                move["product_id"][0] if move["product_id"] else 0,
                                move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                            ],
                            "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                            "lot_id": [
                                (
                                    move["lot_id"][0]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 0
                                    else 0
                                ),
                                (
                                    move["lot_id"][1]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 1
                                    else move["lot_id"] if isinstance(move["lot_id"], str) else ""
                                ),
                            ],
                            "expire_date": (
                                request.env["stock.lot"].sudo().browse(move["lot_id"][0]).expiration_date
                                if move["lot_id"]
                                else ""
                            ),
                            "location_id": move["location_id"],
                            "rimoval_priority": location.priority_picking_desplay,
                            "barcode_location": location.barcode if location else "",
                            "location_dest_id": move["location_dest_id"],
                            "barcode_location_dest": location_dest.barcode if location_dest else "",
                            "quantity": move["product_uom_qty"],
                            "barcode": product.barcode if product else "",
                            "other_barcode": array_all_barcode,
                            "product_packing": array_packing,
                            "weight": product.weight if product else 0,
                            "unidades": product.uom_id.name if product else "",
                            "zona_entrega": delivery_zone_name,
                            "id_zona_entrega": delivery_zone_id,
                            "pedido": picking_name,
                            "pedido_id": picking_id,
                            "origin": picking.origin or "",
                            "quantity_separate": move[
                                "qty_done"
                            ],  # Cantidad separada si el item ya fue separado
                            "observation": move["new_observation"] or "",  # Observación del movimiento
                            "time_separate": move["time"] or "",  # Hora de separación del item
                            "fecha_transaccion": move["date_transaction_picking"]
                            or "",  # Fecha de separación del item
                            # "id_user_separate": user_operator_id,
                            # "user_separate": user_operator_name,
                            "is_separate": (
                                1 if move["is_done_item"] else 0
                            ),  # Indica si el item ya fue separado
                        }
                    )

                if array_batch_temp["list_items"]:
                    array_batch.append(array_batch_temp)

            return {"code": 200, "update_version": update_required, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "update_version": update_required, "msg": f"Acceso denegado: {str(e)}"}

        except Exception as err:
            if "unsupported XML-RPC protocol" in str(err):
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Indicar protocolo http o https de url_rpc",
                }
            return {"code": 400, "update_version": update_required, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/batchs/v2", auth="user", type="json", methods=["GET"])
    def get_batches_v2(self, **kwargs):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            # obtener la configuracion picking de la app
            config_picking = request.env["picking.config.general"].sudo().browse(1)

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            all_location_ids = list(
                {
                    loc_id
                    for zone in user_wms.zone_ids.sudo().read(["location_ids"])
                    for loc_id in zone["location_ids"]
                }
            )

            if not all_location_ids:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(
                    request.env["stock.location"]
                    .sudo()
                    .browse(chunk)
                    .read(
                        [
                            "id",
                            "name",
                            "complete_name",
                            "priority_picking",
                            "barcode",
                            "priority_picking_desplay",
                        ]
                    )
                )

            user_location_ids = [location["id"] for location in locations]

            search_domain = [("state", "=", "in_progress"), ("picking_type_code", "=", "internal")]

            # ✅ Filtrar por responsable si config_picking es 'responsible'
            if config_picking.picking_type == "responsible":
                search_domain.append(("user_id", "=", user.id))  # Agregar filtro por usuario responsable

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search(search_domain)

            # ✅ Verificar si no hay lotes encontrados
            if not batchs:
                return {"code": 200, "msg": "No tienes batches asignados"}

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos unificados
                move_unified_ids = (
                    request.env["move.line.unified"]
                    .sudo()
                    .search(
                        [("stock_picking_batch_id", "=", batch.id), ("location_id", "in", user_location_ids)]
                    )
                )

                if not move_unified_ids:
                    continue

                # ✅ NUEVO: Verificar si todos los items están completados
                total_items = len(move_unified_ids)
                completed_items = len(move_unified_ids.filtered(lambda m: m.is_done_item))

                # Si todos los items están completados, saltar este batch
                if total_items > 0 and completed_items == total_items:
                    continue

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
                origin_details = origins_list if origins_list else []

                # stock_moves = move_unified_ids.read(["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty", "is_done_item"])
                stock_moves = move_unified_ids.read()

                array_batch_temp = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "rol": user_wms.user_rol or "USER",
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "scheduleddate": batch.scheduled_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "muelle": batch.location_id.display_name if batch.location_id else "SIN-MUELLE",
                    "id_muelle": batch.location_id.id if batch.location_id else "",
                    "id_muelle_padre": batch.location_id.location_id.id if batch.location_id else "",
                    "barcode_muelle": batch.location_id.barcode or "",
                    "count_items": len(stock_moves),
                    "total_quantity_items": sum(move["product_uom_qty"] for move in stock_moves),
                    # ✅ NUEVO: Agregar información de progreso
                    "completed_items": completed_items,
                    "progress_percentage": (
                        round((completed_items / total_items) * 100, 2) if total_items > 0 else 0
                    ),
                    "start_time_pick": batch.start_time_pick or "",
                    "end_time_pick": batch.end_time_pick or "",
                    "zona_entrega": (
                        batch.picking_ids[0].delivery_zone_id.name
                        if batch.picking_ids and batch.picking_ids[0].delivery_zone_id
                        else "SIN-ZONA"
                    ),
                    "origin": origin_details,
                    "list_items": [],
                }

                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {
                    prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)
                }

                location_ids = {move["location_id"][0] for move in stock_moves}
                locations_dict = {
                    loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)
                }

                for move in stock_moves:
                    product = products.get(move["product_id"][0])
                    location = locations_dict.get(move["location_id"][0])
                    location_dest = locations_dict.get(move["location_dest_id"][0])

                    # ✅ Obtener códigos de barras adicionales
                    array_all_barcode = (
                        [
                            {
                                "barcode": barcode.name,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": [
                                    move["product_id"][0] if move["product_id"] else 0,
                                    move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                                ],
                                "cantidad": 1,
                                "id_product": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for barcode in product.barcode_ids
                            if barcode.name  # Filtra solo los barcodes válidos
                        ]
                        if product.barcode_ids
                        else []
                    )

                    # ✅ Obtener empaques del producto
                    array_packing = (
                        [
                            {
                                "barcode": pack.barcode,
                                "cantidad": pack.qty,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for pack in product.packaging_ids
                            if pack.barcode
                        ]
                        if product.packaging_ids
                        else []
                    )

                    # ✅ Buscar el picking_id desde stock.move
                    picking = (
                        request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)
                    )  # Obtiene un picking asociado al batch
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener el nombre del pedido
                    picking_name = picking.display_name if picking else ""

                    # ✅ Obtener la zona de entrega del picking
                    delivery_zone_name = (
                        picking.delivery_zone_id.display_name
                        if picking and picking.delivery_zone_id
                        else "SIN-ZONA"
                    )
                    delivery_zone_id = (
                        picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0
                    )

                    user_operator_id = 0
                    user_operator_name = ""
                    if (
                        move.get("user_operator_id")
                        and isinstance(move["user_operator_id"], (list, tuple))
                        and len(move["user_operator_id"]) > 0
                    ):
                        user_operator_id = move["user_operator_id"][0]
                        user_operator_name = (
                            move["user_operator_id"][1] if len(move["user_operator_id"]) > 1 else ""
                        )

                    array_batch_temp["list_items"].append(
                        {
                            "batch_id": batch.id,
                            "id_move": move["id"],
                            "picking_id": picking_id,
                            "id_product": move["product_id"][0] if move["product_id"] else 0,
                            "product_id": [
                                move["product_id"][0] if move["product_id"] else 0,
                                move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                            ],
                            "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                            "lot_id": [
                                (
                                    move["lot_id"][0]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 0
                                    else 0
                                ),
                                (
                                    move["lot_id"][1]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 1
                                    else move["lot_id"] if isinstance(move["lot_id"], str) else ""
                                ),
                            ],
                            "expire_date": (
                                request.env["stock.lot"].sudo().browse(move["lot_id"][0]).expiration_date
                                if move["lot_id"]
                                else ""
                            ),
                            "location_id": move["location_id"],
                            "rimoval_priority": location.priority_picking_desplay,
                            "barcode_location": location.barcode if location else "",
                            "location_dest_id": move["location_dest_id"],
                            "barcode_location_dest": location_dest.barcode if location_dest else "",
                            "quantity": move["product_uom_qty"],
                            "barcode": product.barcode if product else "",
                            "other_barcode": array_all_barcode,
                            "product_packing": array_packing,
                            "weight": product.weight if product else 0,
                            "unidades": product.uom_id.name if product else "",
                            "zona_entrega": delivery_zone_name,
                            "id_zona_entrega": delivery_zone_id,
                            "pedido": picking_name,
                            "pedido_id": picking_id,
                            "origin": picking.origin or "",
                            "quantity_separate": move[
                                "qty_done"
                            ],  # Cantidad separada si el item ya fue separado
                            "observation": move["new_observation"] or "",  # Observación del movimiento
                            "time_separate": move["time"] or "",  # Hora de separación del item
                            "fecha_transaccion": move["date_transaction_picking"]
                            or "",  # Fecha de separación del item
                            # "id_user_separate": user_operator_id,
                            # "user_separate": user_operator_name,
                            "is_separate": (
                                1 if move["is_done_item"] else 0
                            ),  # Indica si el item ya fue separado
                        }
                    )

                if array_batch_temp["list_items"]:
                    array_batch.append(array_batch_temp)

            return {"code": 200, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}

        except Exception as err:
            if "unsupported XML-RPC protocol" in str(err):
                return {"code": 400, "msg": "Indicar protocolo http o https de url_rpc"}
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/batchs/devs", auth="user", type="json", methods=["GET"])
    def get_batches_devs(self, **kwargs):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            # obtener la configuracion picking de la app
            config_picking = request.env["picking.config.general"].sudo().browse(1)

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            all_location_ids = list(
                {
                    loc_id
                    for zone in user_wms.zone_ids.sudo().read(["location_ids"])
                    for loc_id in zone["location_ids"]
                }
            )

            if not all_location_ids:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(
                    request.env["stock.location"]
                    .sudo()
                    .browse(chunk)
                    .read(
                        [
                            "id",
                            "name",
                            "complete_name",
                            "priority_picking",
                            "barcode",
                            "priority_picking_desplay",
                        ]
                    )
                )

            user_location_ids = [location["id"] for location in locations]

            search_domain = [
                ("state", "=", "in_progress"),
                ("picking_type_code", "=", "incoming"),
                ("user_id", "=", user.id),
            ]

            # ✅ Filtrar por responsable si config_picking es 'responsible'
            # if config_picking.picking_type == "responsible":
            #     search_domain.append(("user_id", "=", user.id))  # Agregar filtro por usuario responsable

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search(search_domain)

            # ✅ Verificar si no hay lotes encontrados
            if not batchs:
                return {"code": 200, "msg": "No tienes batches asignados"}

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos unificados
                move_unified_ids = (
                    request.env["move.line.unified"]
                    .sudo()
                    .search(
                        [
                            ("stock_picking_batch_id", "=", batch.id),
                            ("location_id", "in", user_location_ids),
                            ("is_done_item", "=", False),
                        ]
                    )
                )

                if not move_unified_ids:
                    continue

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
                origin_details = origins_list if origins_list else []

                stock_moves = move_unified_ids.read(
                    ["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty"]
                )

                array_batch_temp = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "rol": user_wms.user_rol or "USER",
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "scheduleddate": batch.scheduled_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "muelle": batch.location_id.display_name if batch.location_id else "SIN-MUELLE",
                    "id_muelle": batch.location_id.id if batch.location_id else "",
                    "barcode_muelle": batch.location_id.barcode or "",
                    "count_items": len(stock_moves),
                    "total_quantity_items": sum(move["product_uom_qty"] for move in stock_moves),
                    "start_time_pick": batch.start_time_pick or "",
                    "end_time_pick": batch.end_time_pick or "",
                    "zona_entrega": (
                        batch.picking_ids[0].delivery_zone_id.name
                        if batch.picking_ids and batch.picking_ids[0].delivery_zone_id
                        else "SIN-ZONA"
                    ),
                    # "zona_entrega_tms": batch.picking_ids[0].delivery_zone_tms if batch.picking_ids and batch.picking_ids[0].delivery_zone_tms else "N/A",
                    # "order_tms": batch.picking_ids[0].order_tms if batch.picking_ids and batch.picking_ids[0].order_tms else "N/A",
                    "origin": origin_details,
                    "list_items": [],
                }

                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {
                    prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)
                }

                location_ids = {move["location_id"][0] for move in stock_moves}
                locations_dict = {
                    loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)
                }

                for move in stock_moves:
                    product = products.get(move["product_id"][0])
                    location = locations_dict.get(move["location_id"][0])
                    location_dest = locations_dict.get(move["location_dest_id"][0])

                    # ✅ Obtener códigos de barras adicionales
                    array_all_barcode = (
                        [
                            {
                                "barcode": barcode.name,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": [
                                    move["product_id"][0] if move["product_id"] else 0,
                                    move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                                ],
                                "cantidad": 1,
                                "id_product": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for barcode in product.barcode_ids
                            if barcode.name  # Filtra solo los barcodes válidos
                        ]
                        if product.barcode_ids
                        else []
                    )

                    # ✅ Obtener empaques del producto
                    array_packing = (
                        [
                            {
                                "barcode": pack.barcode,
                                "cantidad": pack.qty,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for pack in product.packaging_ids
                            if pack.barcode
                        ]
                        if product.packaging_ids
                        else []
                    )

                    # ✅ Buscar el picking_id desde stock.move
                    picking = (
                        request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)
                    )  # Obtiene un picking asociado al batch
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener el nombre del pedido
                    picking_name = picking.display_name if picking else ""

                    # ✅ Obtener la zona de entrega del picking
                    delivery_zone_name = (
                        picking.delivery_zone_id.display_name
                        if picking and picking.delivery_zone_id
                        else "SIN-ZONA"
                    )
                    delivery_zone_id = (
                        picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0
                    )

                    array_batch_temp["list_items"].append(
                        {
                            "batch_id": batch.id,
                            "id_move": move["id"],
                            "picking_id": picking_id,
                            "id_product": move["product_id"][0] if move["product_id"] else 0,
                            "product_id": [
                                move["product_id"][0] if move["product_id"] else 0,
                                move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                            ],
                            "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                            "lot_id": [
                                (
                                    move["lot_id"][0]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 0
                                    else 0
                                ),
                                (
                                    move["lot_id"][1]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 1
                                    else move["lot_id"] if isinstance(move["lot_id"], str) else ""
                                ),
                            ],
                            "expire_date": (
                                request.env["stock.lot"].sudo().browse(move["lot_id"][0]).expiration_date
                                if move["lot_id"]
                                else ""
                            ),
                            "location_id": move["location_id"],
                            # "rimoval_priority": location.priority_picking,
                            "rimoval_priority": location.priority_picking_desplay,
                            "barcode_location": location.barcode if location else "",
                            "location_dest_id": move["location_dest_id"],
                            "barcode_location_dest": location_dest.barcode if location_dest else "",
                            "quantity": move["product_uom_qty"],
                            "barcode": product.barcode if product else "",
                            "other_barcode": array_all_barcode,
                            "product_packing": array_packing,
                            "weight": product.weight if product else 0,
                            "unidades": product.uom_id.name if product else "",
                            "zona_entrega": delivery_zone_name,
                            "id_zona_entrega": delivery_zone_id,
                            "pedido": picking_name,
                            "pedido_id": picking_id,
                            "origin": picking.origin or "",
                        }
                    )

                if array_batch_temp["list_items"]:
                    array_batch.append(array_batch_temp)

            return {"code": 200, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}

        except Exception as err:
            if "unsupported XML-RPC protocol" in str(err):
                return {"code": 400, "msg": "Indicar protocolo http o https de url_rpc"}
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/batchs/devs/v2", auth="user", type="json", methods=["GET"])
    def get_batches_devs_v2(self, **kwargs):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            # obtener la configuracion picking de la app
            config_picking = request.env["picking.config.general"].sudo().browse(1)

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            all_location_ids = list(
                {
                    loc_id
                    for zone in user_wms.zone_ids.sudo().read(["location_ids"])
                    for loc_id in zone["location_ids"]
                }
            )

            if not all_location_ids:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(
                    request.env["stock.location"]
                    .sudo()
                    .browse(chunk)
                    .read(
                        [
                            "id",
                            "name",
                            "complete_name",
                            "priority_picking",
                            "barcode",
                            "priority_picking_desplay",
                        ]
                    )
                )

            user_location_ids = [location["id"] for location in locations]

            search_domain = [
                ("state", "=", "in_progress"),
                ("picking_type_code", "=", "incoming"),
                ("user_id", "=", user.id),
            ]

            # ✅ Filtrar por responsable si config_picking es 'responsible'
            # if config_picking.picking_type == "responsible":
            #     search_domain.append(("user_id", "=", user.id))  # Agregar filtro por usuario responsable

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search(search_domain)

            # ✅ Verificar si no hay lotes encontrados
            if not batchs:
                return {"code": 200, "msg": "No tienes batches asignados"}

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos unificados
                move_unified_ids = (
                    request.env["move.line.unified"]
                    .sudo()
                    .search(
                        [
                            ("stock_picking_batch_id", "=", batch.id),
                            ("location_id", "in", user_location_ids),
                            ("is_done_item", "=", False),
                        ]
                    )
                )

                if not move_unified_ids:
                    continue

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
                origin_details = origins_list if origins_list else []

                stock_moves = move_unified_ids.read(
                    ["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty"]
                )

                array_batch_temp = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "rol": user_wms.user_rol or "USER",
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "scheduleddate": batch.scheduled_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "muelle": batch.location_id.display_name if batch.location_id else "SIN-MUELLE",
                    "id_muelle": batch.location_id.id if batch.location_id else "",
                    "barcode_muelle": batch.location_id.barcode or "",
                    "count_items": len(stock_moves),
                    "total_quantity_items": sum(move["product_uom_qty"] for move in stock_moves),
                    "start_time_pick": batch.start_time_pick or "",
                    "end_time_pick": batch.end_time_pick or "",
                    "zona_entrega": (
                        batch.picking_ids[0].delivery_zone_id.name
                        if batch.picking_ids and batch.picking_ids[0].delivery_zone_id
                        else "SIN-ZONA"
                    ),
                    # "zona_entrega_tms": batch.picking_ids[0].delivery_zone_tms if batch.picking_ids and batch.picking_ids[0].delivery_zone_tms else "N/A",
                    # "order_tms": batch.picking_ids[0].order_tms if batch.picking_ids and batch.picking_ids[0].order_tms else "N/A",
                    "origin": origin_details,
                    "list_items": [],
                }

                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {
                    prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)
                }

                location_ids = {move["location_id"][0] for move in stock_moves}
                locations_dict = {
                    loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)
                }

                for move in stock_moves:
                    product = products.get(move["product_id"][0])
                    location = locations_dict.get(move["location_id"][0])
                    location_dest = locations_dict.get(move["location_dest_id"][0])

                    # ✅ Obtener códigos de barras adicionales
                    array_all_barcode = (
                        [
                            {
                                "barcode": barcode.name,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": [
                                    move["product_id"][0] if move["product_id"] else 0,
                                    move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                                ],
                                "cantidad": 1,
                                "id_product": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for barcode in product.barcode_ids
                            if barcode.name  # Filtra solo los barcodes válidos
                        ]
                        if product.barcode_ids
                        else []
                    )

                    # ✅ Obtener empaques del producto
                    array_packing = (
                        [
                            {
                                "barcode": pack.barcode,
                                "cantidad": pack.qty,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for pack in product.packaging_ids
                            if pack.barcode
                        ]
                        if product.packaging_ids
                        else []
                    )

                    # ✅ Buscar el picking_id desde stock.move
                    picking = (
                        request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)
                    )  # Obtiene un picking asociado al batch
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener el nombre del pedido
                    picking_name = picking.display_name if picking else ""

                    # ✅ Obtener la zona de entrega del picking
                    delivery_zone_name = (
                        picking.delivery_zone_id.display_name
                        if picking and picking.delivery_zone_id
                        else "SIN-ZONA"
                    )
                    delivery_zone_id = (
                        picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0
                    )

                    array_batch_temp["list_items"].append(
                        {
                            "batch_id": batch.id,
                            "id_move": move["id"],
                            "picking_id": picking_id,
                            "id_product": move["product_id"][0] if move["product_id"] else 0,
                            "product_id": [
                                move["product_id"][0] if move["product_id"] else 0,
                                move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                            ],
                            "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                            "lot_id": [
                                (
                                    move["lot_id"][0]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 0
                                    else 0
                                ),
                                (
                                    move["lot_id"][1]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 1
                                    else move["lot_id"] if isinstance(move["lot_id"], str) else "N/A"
                                ),
                            ],
                            "expire_date": (
                                request.env["stock.lot"].sudo().browse(move["lot_id"][0]).expiration_date
                                if move["lot_id"]
                                else ""
                            ),
                            "location_id": move["location_id"],
                            # "rimoval_priority": location.priority_picking,
                            "rimoval_priority": location.priority_picking_desplay,
                            "barcode_location": location.barcode if location else "",
                            "location_dest_id": move["location_dest_id"],
                            "barcode_location_dest": location_dest.barcode if location_dest else "",
                            "quantity": move["product_uom_qty"],
                            "barcode": product.barcode if product else "",
                            "other_barcode": array_all_barcode,
                            "product_packing": array_packing,
                            "weight": product.weight if product else 0,
                            "unidades": product.uom_id.name if product else "",
                            "zona_entrega": delivery_zone_name,
                            "id_zona_entrega": delivery_zone_id,
                            "pedido": picking_name,
                            "pedido_id": picking_id,
                            "origin": picking.origin or "",
                        }
                    )

                if array_batch_temp["list_items"]:
                    array_batch.append(array_batch_temp)

            return {"code": 200, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}

        except Exception as err:
            if "unsupported XML-RPC protocol" in str(err):
                return {"code": 400, "msg": "Indicar protocolo http o https de url_rpc"}
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Transacciones batchs para picking por ID
    @http.route("/api/batch/<int:id_batch>", auth="user", type="json", methods=["GET"])
    def get_batch_by_id(self, id_batch):
        try:
            user = request.env.user

            # ✅ Validar usuario autenticado
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            zone_locations = user_wms.zone_ids.sudo().read(["name", "warehouse_id", "location_ids"])

            all_location_ids = []
            for zone in zone_locations:
                all_location_ids.extend(zone["location_ids"])

            all_location_ids = list(set(all_location_ids))  # Eliminar duplicados

            # ✅ Obtener ubicaciones por bloques para evitar sobrecarga
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(
                    request.env["stock.location"].sudo().browse(chunk).read(["id", "name", "complete_name"])
                )

            if not locations:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            user_location_ids = [location["id"] for location in locations]

            # ✅ Obtener información del batch específico
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 404, "msg": "Batch no encontrado"}

            # ✅ Validar si el batch tiene movimientos unificados
            move_unified_ids = (
                request.env["move.line.unified"]
                .sudo()
                .search(
                    [
                        ("stock_picking_batch_id", "=", batch.id),
                        ("is_done_item", "=", True),
                        ("user_operator_id", "=", user.id),
                    ]
                )
            )

            stock_moves = move_unified_ids.read()

            array_batch_temp = {
                "id": batch.id,
                "name": batch.name or "",
                "user_name": user.name,
                "user_id": user.id,
                "rol": user_wms.user_rol or "USER",
                "order_by": picking_strategy.picking_priority_app if picking_strategy else "",
                "order_picking": picking_strategy.picking_order_app if picking_strategy else "",
                "scheduleddate": batch.scheduled_date or "",
                "state": batch.state or "",
                "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                "observation": "",
                "is_wave": batch.is_wave,
                "muelle": batch.location_id.display_name if batch.location_id else "SIN-MUELLE",
                "id_muelle": batch.location_id.id if batch.location_id else "",
                "count_items": len(stock_moves),
                "total_quantity_items": sum(move["product_uom_qty"] for move in stock_moves),
                "items_separado": sum(move["qty_done"] for move in stock_moves),
                "start_time_pick": batch.start_time_pick or "",
                "end_time_pick": batch.end_time_pick or "",
                "zona_entrega": (
                    batch.picking_ids[0].delivery_zone_id.name
                    if batch.picking_ids and batch.picking_ids[0].delivery_zone_id
                    else "SIN-ZONA"
                ),
                "list_items": [],
            }

            # ✅ Procesar movimientos unificados
            for move in stock_moves:
                product = request.env["product.product"].sudo().browse(move["product_id"][0])
                location = request.env["stock.location"].sudo().browse(move["location_id"][0])
                location_dest = request.env["stock.location"].sudo().browse(move["location_dest_id"][0])

                # Obtener códigos de barras adicionales
                array_all_barcode = []
                if product.barcode_ids:
                    for barcode in product.barcode_ids:
                        if barcode.name:  # Verifica si el barcode es válido
                            array_all_barcode.append(
                                {
                                    "barcode": barcode.name,
                                    "batch_id": batch.id,
                                    "id_move": move["id"],
                                    "product_id": [
                                        move["product_id"][0] if move["product_id"] else 0,
                                        move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                                    ],
                                    "cantidad": 1,
                                    "id_product": move["product_id"][0] if move["product_id"] else 0,
                                }
                            )

                # Obtener empaques del producto
                array_packing = []
                if product.packaging_ids:
                    for pack in product.packaging_ids:
                        if pack.barcode:  # Verifica si el barcode es válido
                            array_packing.append(
                                {
                                    "barcode": pack.barcode,
                                    "cantidad": pack.qty,
                                    "batch_id": batch.id,
                                    "id_move": move["id"],
                                    "product_id": move["product_id"][0] if move["product_id"] else 0,
                                }
                            )

                # Obtener fecha de vencimiento (lote)
                expire_date = ""
                if move["lot_id"]:
                    lot = request.env["stock.lot"].sudo().browse(move["lot_id"][0])
                    expire_date = lot.expiration_date if lot else ""

                # ✅ Buscar el picking_id desde stock.move
                picking = (
                    request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)
                )  # Obtiene un picking asociado al batch
                picking_id = picking.id if picking else 0

                # ✅ Obtener el nombre del pedido
                picking_name = picking.display_name if picking else ""

                # ✅ Obtener la zona de entrega del picking
                delivery_zone_name = (
                    picking.delivery_zone_id.display_name
                    if picking and picking.delivery_zone_id
                    else "SIN-ZONA"
                )
                delivery_zone_id = picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0

                array_batch_temp["list_items"].append(
                    {
                        "batch_id": batch.id,
                        "id_move": move["id"],
                        "picking_id": picking_id,
                        "id_product": move["product_id"][0] if move["product_id"] else 0,
                        "product_id": [
                            move["product_id"][0] if move["product_id"] else 0,
                            move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                        ],
                        "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                        "lot_id": [
                            move["lot_id"][0] if move["lot_id"] else "",
                            move["lot_id"][1] if move["lot_id"] else "N/A",
                        ],
                        "expire_date": expire_date,
                        "location_id": move["location_id"],
                        # "rimoval_priority": location.priority_picking,
                        "rimoval_priority": location.priority_picking_desplay,
                        "barcode_location": location.barcode if location.barcode else "",
                        "location_dest_id": move["location_dest_id"],
                        "barcode_location_dest": location_dest.barcode if location_dest.barcode else "",
                        "quantity": move["product_uom_qty"],
                        "quantity_done": move["qty_done"],
                        "fecha_transaccion": move["date_transaction_picking"] or "",
                        "observation": move["new_observation"] or "",
                        "time_line": move["time"] or "",
                        "operator_id": move["user_operator_id"][0] if move["user_operator_id"] else 0,
                        "done_item": move["is_done_item"],
                        "barcode": product.barcode,
                        "other_barcode": array_all_barcode,
                        "product_packing": array_packing,
                        "weight": product.weight,
                        "unidades": product.uom_id.name,
                        "zona_entrega": delivery_zone_name,
                        "id_zona_entrega": delivery_zone_id,
                        "pedido": picking_name,
                        "pedido_id": picking_id,
                    }
                )

            return {"code": 200, "result": array_batch_temp}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    # POST Transacciones enviar cantidades para valores unificados - send batch picking
    @http.route("/api/send_batch", auth="user", type="json", methods=["POST"])
    def send_batch(self, **auth):
        try:
            # ✅ Autenticación del usuario
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            id_batch = auth.get("id_batch")
            list_item = auth.get("list_item", [])
            total_time = 0  # Inicializar tiempo total

            # ✅ Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"No se encontró el id_batch: {id_batch}"}

            array_result = []

            # ✅ Iterar sobre los movimientos de la lista
            for move in list_item:
                id_move = move.get("id_move")
                cantidad = move.get("cantidad")
                novedad = move.get("novedad", "")
                time_line = int(move.get("time_line", 0))
                muelle = move.get("muelle")
                id_operario = move.get("id_operario")
                fecha_transaccion = move.get("fecha_transaccion", "")

                # Validar si el id_move existe
                move_unified = request.env["move.line.unified"].sudo().browse(id_move)
                if not move_unified.exists():
                    array_result.append({"error": f"No se encontró este id_move: {id_move}"})
                    continue

                # ✅ Formatear tiempo en 'HH:MM:SS'
                total_time += time_line
                hours = time_line // 3600
                minutes = (time_line % 3600) // 60
                seconds = time_line % 60
                formatted_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

                # si novedad viene vacio agregar el valor por defecto "Sin novedad"
                if not novedad:
                    novedad = "Sin novedad"

                # ✅ Actualizar movimiento
                update_values = {
                    "qty_done": cantidad,
                    "new_observation": novedad,
                    "time": formatted_time,
                    "location_dest_id": muelle,
                    "is_done_item": True,
                    "date_transaction_picking": (
                        procesar_fecha_naive(fecha_transaccion, "America/Bogota")
                        if fecha_transaccion
                        else datetime.now(pytz.utc)
                    ),
                }

                if id_operario:
                    update_values["user_operator_id"] = id_operario

                move_unified.write(update_values)

                array_result.append(
                    {
                        "id_move": id_move,
                        "id_batch": id_batch,
                        "id_product": move_unified.product_id.id,
                        "complete": f"Se actualizó correctamente el id_move: {id_move}",
                    }
                )

            # ✅ Formatear tiempo total en 'HH:MM:SS'
            total_hours = total_time // 3600
            total_minutes = (total_time % 3600) // 60
            total_seconds = total_time % 60
            # total_time_formatted = f"{total_hours:02d}:{total_minutes:02d}:{total_seconds:02d}"

            total_time_formatted = total_hours + (total_minutes / 60) + (total_seconds / 3600)

            # ✅ Actualizar tiempo total en el batch
            batch.write({"time_batch": total_time_formatted})

            if any("error" in result for result in array_result):
                return {"code": 400, "result": array_result}
            else:
                return {"code": 200, "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            if "unsupported XML-RPC protocol" in str(err):
                return {"code": 400, "msg": "Indicar protocolo http o https de url_rpc"}

    #         return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/send_batch/2", auth="user", type="json", methods=["POST"])
    def send_batch_2(self, **auth):
        try:
            # ✅ Usar savepoint para revertir todo si falla una validación de stock
            with request.env.cr.savepoint():
                user = request.env.user
                if not user:
                    return {"code": 401, "msg": "Usuario no autenticado"}

                id_batch = auth.get("id_batch")
                list_item = auth.get("list_item", [])
                total_time = 0

                # 1. ENCONTRAR EL BATCH
                batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
                if not batch.exists():
                    return {"code": 400, "msg": f"No se encontró el id_batch: {id_batch}"}

                # ✅ 2. LISTA DE PICKINGS PERMITIDOS (LA CLAVE DE LA SOLUCIÓN)
                # Obtenemos TODOS los IDs de los pickings que pertenecen a este batch.
                # Cualquier reserva hecha por estos documentos se considerará "Mía".
                allowed_picking_ids = batch.picking_ids.ids

                # ==========================================
                # FUNCIONES AUXILIARES
                # ==========================================

                def obtener_transferencias_con_reservas(product_id, location_id, lote_id=None):
                    domain = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        (
                            "picking_id.state",
                            "in",
                            ["assigned", "confirmed", "waiting", "partially_available", "draft"],
                        ),
                    ]
                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    move_lines = request.env["stock.move.line"].sudo().search(domain)
                    pickings_info = {}
                    for ml in move_lines:
                        picking = ml.picking_id
                        if picking.id not in pickings_info:
                            pickings_info[picking.id] = {
                                "nombre": picking.name,
                                "cantidad_reservada": 0,
                            }
                        pickings_info[picking.id]["cantidad_reservada"] += ml.quantity
                    return list(pickings_info.values())

                def validar_stock_disponible(
                    product_id, location_id, cantidad_requerida, lote_id=None, allowed_pickings=None
                ):
                    """
                    Valida stock.
                    allowed_pickings: Lista de IDs de pickings del Batch actual.
                    Si la reserva es de uno de estos pickings, se ignora (se considera disponible).
                    """
                    if allowed_pickings is None:
                        allowed_pickings = []

                    # 1. Stock Físico Total
                    domain = [("product_id", "=", product_id), ("location_id", "=", location_id)]
                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    quants = request.env["stock.quant"].sudo().search(domain)
                    stock_total = sum(q.quantity for q in quants)

                    # 2. Calcular Reservas
                    domain_moves = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        (
                            "picking_id.state",
                            "in",
                            ["assigned", "confirmed", "waiting", "partially_available"],
                        ),
                    ]
                    if lote_id:
                        domain_moves.append(("lot_id", "=", lote_id))

                    todas_move_lines = request.env["stock.move.line"].sudo().search(domain_moves)

                    stock_reservado_mi_batch = 0
                    stock_reservado_otros = 0
                    ids_picking = []

                    for ml in todas_move_lines:
                        # ✅ LOGICA MAESTRA:
                        # Si el picking dueño de la reserva está en mi lista de permitidos (mi batch),
                        # entonces esa reserva es para mí.
                        ids_picking.append(ml.picking_id.id)
                        if ml.picking_id.id in allowed_pickings:
                            stock_reservado_mi_batch += ml.quantity
                        else:
                            stock_reservado_otros += ml.quantity

                    ids_picking = list(set(ids_picking))

                    # Stock disponible = Físico - Reservas de TERCEROS
                    # (No resto 'stock_reservado_mi_batch' porque esa reserva es justamente para consumir ahora)
                    stock_disponible = stock_total - stock_reservado_otros

                    return {
                        "stock_disponible": stock_disponible,
                        "stock_total": stock_total,
                        "stock_reservado_otros": stock_reservado_otros,
                        "stock_reservado_esta": stock_reservado_mi_batch,
                        "es_suficiente": stock_disponible >= cantidad_requerida,
                    }

                # ==========================================
                # PROCESAMIENTO
                # ==========================================

                array_result = []

                for move_data in list_item:
                    id_move = move_data.get("id_move")
                    cantidad_enviada = float(move_data.get("cantidad", 0))
                    novedad = move_data.get("novedad", "") or "Sin novedad"
                    time_line = int(move_data.get("time_line", 0))
                    muelle_id = move_data.get("muelle")
                    id_operario = move_data.get("id_operario")
                    fecha_transaccion = move_data.get("fecha_transaccion", "")

                    # Buscar en modelo unificado
                    move_unified = request.env["move.line.unified"].sudo().browse(id_move)
                    if not move_unified.exists():
                        return {"code": 400, "msg": f"No se encontró este id_move: {id_move}"}

                    location_origen = move_unified.location_id
                    product = move_unified.product_id
                    lote = move_unified.lot_id

                    # --- INICIO VALIDACIÓN DE STOCK ---
                    validacion_stock = validar_stock_disponible(
                        product_id=product.id,
                        location_id=location_origen.id,
                        cantidad_requerida=cantidad_enviada,
                        lote_id=lote.id if lote else None,
                        allowed_pickings=allowed_picking_ids,  # ✅ Enviamos la lista completa del batch
                    )

                    if not validacion_stock["es_suficiente"]:
                        transferencias_con_reservas = obtener_transferencias_con_reservas(
                            product_id=product.id,
                            location_id=location_origen.id,
                            lote_id=lote.id if lote else None,
                        )

                        # 🎨 Mensaje de Error
                        sku_lote = f"SKU/Lote: {product.default_code or 'N/A'}"
                        if lote:
                            sku_lote += f" / {lote.name}"

                        mensaje_error = f"{sku_lote}\n"
                        mensaje_error += f"Descripción: {product.name}\n"
                        mensaje_error += (
                            f"Ubicación: {location_origen.complete_name or location_origen.display_name}\n"
                        )
                        mensaje_error += f"Estado de Stock (Req. {cantidad_enviada})\n\n"

                        mensaje_error += f"Disponible: {validacion_stock['stock_disponible']}\n"
                        mensaje_error += f"* Inventario Teórico: {validacion_stock['stock_total']}\n"

                        reserva_total = (
                            validacion_stock["stock_reservado_esta"]
                            + validacion_stock["stock_reservado_otros"]
                        )
                        mensaje_error += f"* Reserva Total: {reserva_total}\n"

                        if transferencias_con_reservas:
                            mensaje_error += "Documentos con reserva:\n"
                            for tf in transferencias_con_reservas:
                                nombre_corto = (
                                    tf["nombre"].split("/")[-2:] if "/" in tf["nombre"] else [tf["nombre"]]
                                )
                                nombre_corto = "/".join(nombre_corto)
                                mensaje_error += f"* {nombre_corto} {tf['cantidad_reservada']}\n"

                        mensaje_error += (
                            f"\nAcción Requerida:\n"
                            f"* Validar Físico (contra 360WMS)\n"
                            f"* Abastecer/Trasladar a la ubicación indicada.\n"
                            f"* Anular reservas otros documentos (si aplica).\n"
                        )

                        return {
                            "code": 409,
                            "tipo": "STOCK_INSUFICIENTE",
                            "msg": mensaje_error,
                        }
                    # --- FIN VALIDACIÓN DE STOCK ---

                    # Cálculos de tiempo
                    total_time += time_line
                    hours = time_line // 3600
                    minutes = (time_line % 3600) // 60
                    seconds = time_line % 60
                    formatted_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

                    fecha_dt = (
                        procesar_fecha_naive(fecha_transaccion, "America/Bogota")
                        if fecha_transaccion
                        else datetime.now(pytz.utc)
                    )

                    update_values = {
                        "qty_done": cantidad_enviada,
                        "new_observation": novedad,
                        "time": formatted_time,
                        "location_dest_id": muelle_id,
                        "is_done_item": True,
                        "date_transaction_picking": fecha_dt,
                    }

                    if id_operario:
                        update_values["user_operator_id"] = id_operario

                    move_unified.write(update_values)

                    array_result.append(
                        {
                            "id_move": id_move,
                            "id_batch": id_batch,
                            "id_product": product.id,
                            "complete": f"Se actualizó correctamente el id_move: {id_move}",
                        }
                    )

                # ==========================================
                # FINALIZAR
                # ==========================================

                total_hours = total_time // 3600
                total_minutes = (total_time % 3600) // 60
                total_seconds = total_time % 60
                total_time_float = total_hours + (total_minutes / 60) + (total_seconds / 3600)

                batch.write({"time_batch": total_time_float})

                return {"code": 200, "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/send_batch/componentes", auth="user", type="json", methods=["POST"])
    def send_batch_componentes(self, **auth):
        try:
            # ✅ Usar savepoint para revertir todo si falla una validación de stock
            with request.env.cr.savepoint():
                user = request.env.user
                if not user:
                    return {"code": 401, "msg": "Usuario no autenticado"}

                id_batch = auth.get("id_batch")
                list_item = auth.get("list_item", [])
                total_time = 0

                # 1. ENCONTRAR EL BATCH
                batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
                if not batch.exists():
                    return {"code": 400, "msg": f"No se encontró el id_batch: {id_batch}"}

                # ✅ 2. LISTA DE PICKINGS PERMITIDOS (LA CLAVE DE LA SOLUCIÓN)
                # Obtenemos TODOS los IDs de los pickings que pertenecen a este batch.
                # Cualquier reserva hecha por estos documentos se considerará "Mía".
                allowed_picking_ids = batch.picking_ids.ids

                # ==========================================
                # FUNCIONES AUXILIARES
                # ==========================================

                def obtener_transferencias_con_reservas(product_id, location_id, lote_id=None):
                    domain = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        (
                            "picking_id.state",
                            "in",
                            ["assigned", "confirmed", "waiting", "partially_available", "draft"],
                        ),
                    ]
                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    move_lines = request.env["stock.move.line"].sudo().search(domain)
                    pickings_info = {}
                    for ml in move_lines:
                        picking = ml.picking_id
                        if picking.id not in pickings_info:
                            pickings_info[picking.id] = {
                                "nombre": picking.name,
                                "cantidad_reservada": 0,
                            }
                        pickings_info[picking.id]["cantidad_reservada"] += ml.quantity
                    return list(pickings_info.values())

                def validar_stock_disponible(
                    product_id, location_id, cantidad_requerida, lote_id=None, allowed_pickings=None
                ):
                    """
                    Valida stock.
                    allowed_pickings: Lista de IDs de pickings del Batch actual.
                    Si la reserva es de uno de estos pickings, se ignora (se considera disponible).
                    """
                    if allowed_pickings is None:
                        allowed_pickings = []

                    # 1. Stock Físico Total
                    domain = [("product_id", "=", product_id), ("location_id", "=", location_id)]
                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    quants = request.env["stock.quant"].sudo().search(domain)
                    stock_total = sum(q.quantity for q in quants)

                    # 2. Calcular Reservas
                    domain_moves = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        (
                            "picking_id.state",
                            "in",
                            ["assigned", "confirmed", "waiting", "partially_available"],
                        ),
                    ]
                    if lote_id:
                        domain_moves.append(("lot_id", "=", lote_id))

                    todas_move_lines = request.env["stock.move.line"].sudo().search(domain_moves)

                    stock_reservado_mi_batch = 0
                    stock_reservado_otros = 0
                    ids_picking = []

                    for ml in todas_move_lines:
                        # ✅ LOGICA MAESTRA:
                        # Si el picking dueño de la reserva está en mi lista de permitidos (mi batch),
                        # entonces esa reserva es para mí.
                        ids_picking.append(ml.picking_id.id)
                        if ml.picking_id.id in allowed_pickings:
                            stock_reservado_mi_batch += ml.quantity
                        else:
                            stock_reservado_otros += ml.quantity

                    ids_picking = list(set(ids_picking))

                    # Stock disponible = Físico - Reservas de TERCEROS
                    # (No resto 'stock_reservado_mi_batch' porque esa reserva es justamente para consumir ahora)
                    stock_disponible = stock_total - stock_reservado_otros

                    return {
                        "stock_disponible": stock_disponible,
                        "stock_total": stock_total,
                        "stock_reservado_otros": stock_reservado_otros,
                        "stock_reservado_esta": stock_reservado_mi_batch,
                        "es_suficiente": stock_disponible >= cantidad_requerida,
                    }

                # ==========================================
                # PROCESAMIENTO
                # ==========================================

                array_result = []

                for move_data in list_item:
                    id_move = move_data.get("id_move")
                    cantidad_enviada = float(move_data.get("cantidad", 0))
                    novedad = move_data.get("novedad", "") or "Sin novedad"
                    time_line = int(move_data.get("time_line", 0))
                    muelle_id = move_data.get("muelle")
                    id_operario = move_data.get("id_operario")
                    fecha_transaccion = move_data.get("fecha_transaccion", "")

                    # Buscar en modelo unificado
                    move_unified = request.env["move.line.unified"].sudo().browse(id_move)
                    if not move_unified.exists():
                        return {"code": 400, "msg": f"No se encontró este id_move: {id_move}"}

                    location_origen = move_unified.location_id
                    product = move_unified.product_id
                    lote = move_unified.lot_id

                    # --- INICIO VALIDACIÓN DE STOCK ---
                    validacion_stock = validar_stock_disponible(
                        product_id=product.id,
                        location_id=location_origen.id,
                        cantidad_requerida=cantidad_enviada,
                        lote_id=lote.id if lote else None,
                        allowed_pickings=allowed_picking_ids,  # ✅ Enviamos la lista completa del batch
                    )

                    if not validacion_stock["es_suficiente"]:
                        transferencias_con_reservas = obtener_transferencias_con_reservas(
                            product_id=product.id,
                            location_id=location_origen.id,
                            lote_id=lote.id if lote else None,
                        )

                        # 🎨 Mensaje de Error
                        sku_lote = f"SKU/Lote: {product.default_code or 'N/A'}"
                        if lote:
                            sku_lote += f" / {lote.name}"

                        mensaje_error = f"{sku_lote}\n"
                        mensaje_error += f"Descripción: {product.name}\n"
                        mensaje_error += (
                            f"Ubicación: {location_origen.complete_name or location_origen.display_name}\n"
                        )
                        mensaje_error += f"Estado de Stock (Req. {cantidad_enviada})\n\n"

                        mensaje_error += f"Disponible: {validacion_stock['stock_disponible']}\n"
                        mensaje_error += f"* Inventario Teórico: {validacion_stock['stock_total']}\n"

                        reserva_total = (
                            validacion_stock["stock_reservado_esta"]
                            + validacion_stock["stock_reservado_otros"]
                        )
                        mensaje_error += f"* Reserva Total: {reserva_total}\n"

                        if transferencias_con_reservas:
                            mensaje_error += "Documentos con reserva:\n"
                            for tf in transferencias_con_reservas:
                                nombre_corto = (
                                    tf["nombre"].split("/")[-2:] if "/" in tf["nombre"] else [tf["nombre"]]
                                )
                                nombre_corto = "/".join(nombre_corto)
                                mensaje_error += f"* {nombre_corto} {tf['cantidad_reservada']}\n"

                        mensaje_error += (
                            f"\nAcción Requerida:\n"
                            f"* Validar Físico (contra 360WMS)\n"
                            f"* Abastecer/Trasladar a la ubicación indicada.\n"
                            f"* Anular reservas otros documentos (si aplica).\n"
                        )

                        return {
                            "code": 409,
                            "tipo": "STOCK_INSUFICIENTE",
                            "msg": mensaje_error,
                        }
                    # --- FIN VALIDACIÓN DE STOCK ---

                    # Cálculos de tiempo
                    total_time += time_line
                    hours = time_line // 3600
                    minutes = (time_line % 3600) // 60
                    seconds = time_line % 60
                    formatted_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

                    fecha_dt = (
                        procesar_fecha_naive(fecha_transaccion, "America/Bogota")
                        if fecha_transaccion
                        else datetime.now(pytz.utc)
                    )

                    update_values = {
                        "qty_done": cantidad_enviada,
                        "new_observation": novedad,
                        "time": formatted_time,
                        "location_dest_id": muelle_id,
                        "is_done_item": True,
                        "date_transaction_picking": fecha_dt,
                    }

                    if id_operario:
                        update_values["user_operator_id"] = id_operario

                    move_unified.write(update_values)

                    array_result.append(
                        {
                            "id_move": id_move,
                            "id_batch": id_batch,
                            "id_product": product.id,
                            "complete": f"Se actualizó correctamente el id_move: {id_move}",
                        }
                    )

                # ==========================================
                # FINALIZAR
                # ==========================================

                total_hours = total_time // 3600
                total_minutes = (total_time % 3600) // 60
                total_seconds = total_time % 60
                total_time_float = total_hours + (total_minutes / 60) + (total_seconds / 3600)

                batch.write({"time_batch": total_time_float})

                return {"code": 200, "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Transacciones batchs realizadas por usuario
    @http.route("/api/batchs_done", auth="user", type="json", methods=["GET"])
    def get_batches_done(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario autenticado
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            fecha_batch = auth.get("fecha_batch", datetime.now().strftime("%Y-%m-%d"))

            print(fecha_batch)

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            all_location_ids = list(
                {
                    loc_id
                    for zone in user_wms.zone_ids.sudo().read(["location_ids"])
                    for loc_id in zone["location_ids"]
                }
            )

            if not all_location_ids:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(
                    request.env["stock.location"]
                    .sudo()
                    .browse(chunk)
                    .read(
                        [
                            "id",
                            "name",
                            "complete_name",
                            "priority_picking",
                            "barcode",
                            "priority_picking_desplay",
                        ]
                    )
                )

            user_location_ids = [location["id"] for location in locations]

            state_batch = ["done", "in_progress"]

            fecha_inicio = datetime.strptime(fecha_batch + " 00:00:00", "%Y-%m-%d %H:%M:%S")
            fecha_fin = datetime.strptime(fecha_batch + " 23:59:59", "%Y-%m-%d %H:%M:%S")

            # ✅ Obtener lotes (batches)
            batchs = (
                request.env["stock.picking.batch"]
                .sudo()
                .search(
                    [
                        ("state", "in", state_batch),
                        ("picking_type_code", "=", "internal"),
                        ("write_date", ">=", fecha_inicio),
                        ("write_date", "<=", fecha_fin),
                    ]
                )
            )

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos unificados
                move_unified_ids = (
                    request.env["move.line.unified"]
                    .sudo()
                    .search(
                        [
                            ("stock_picking_batch_id", "=", batch.id),
                            ("is_done_item", "=", True),
                            ("user_operator_id", "=", user.id),
                        ]
                    )
                )

                if not move_unified_ids:
                    continue

                stock_moves = move_unified_ids.read(
                    ["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty", "qty_done"]
                )

                array_batch_temp = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "rol": user_wms.user_rol or "USER",
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "scheduleddate": batch.scheduled_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "muelle": batch.location_id.display_name if batch.location_id else "SIN-MUELLE",
                    "id_muelle": batch.location_id.id if batch.location_id else "",
                    "count_items": len(stock_moves),
                    "total_quantity_items": sum(move["product_uom_qty"] for move in stock_moves),
                    "items_separado": sum(move["qty_done"] for move in stock_moves),
                }

                array_batch.append(array_batch_temp)

            return {"code": 200, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}


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
