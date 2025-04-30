# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, timedelta
import pytz


class TransaccionDataPicking(http.Controller):

    ## GET Transacciones batchs para picking
    @http.route("/api/batchs", auth="user", type="json", methods=["GET"])
    def get_batches(self):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # obtener la configuracion picking de la app
            config_picking = request.env["picking.config.general"].sudo().browse(1)

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            all_location_ids = list({loc_id for zone in user_wms.zone_ids.sudo().read(["location_ids"]) for loc_id in zone["location_ids"]})

            if not all_location_ids:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(request.env["stock.location"].sudo().browse(chunk).read(["id", "name", "complete_name", "priority_picking", "barcode", "priority_picking_desplay"]))

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
                move_unified_ids = request.env["move.line.unified"].sudo().search([("stock_picking_batch_id", "=", batch.id), ("location_id", "in", user_location_ids), ("is_done_item", "=", False)])

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

                stock_moves = move_unified_ids.read(["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty"])

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
                    "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "SIN-ZONA",
                    # "zona_entrega_tms": batch.picking_ids[0].delivery_zone_tms if batch.picking_ids and batch.picking_ids[0].delivery_zone_tms else "N/A",
                    # "order_tms": batch.picking_ids[0].order_tms if batch.picking_ids and batch.picking_ids[0].order_tms else "N/A",
                    "origin": origin_details,
                    "list_items": [],
                }

                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)}

                location_ids = {move["location_id"][0] for move in stock_moves}
                locations_dict = {loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)}

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
                                "product_id": [move["product_id"][0] if move["product_id"] else 0, move["product_id"][1] if len(move["product_id"]) > 1 else "N/A"],
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
                    picking = request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)  # Obtiene un picking asociado al batch
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener el nombre del pedido
                    picking_name = picking.display_name if picking else ""

                    # ✅ Obtener la zona de entrega del picking
                    delivery_zone_name = picking.delivery_zone_id.display_name if picking and picking.delivery_zone_id else "SIN-ZONA"
                    delivery_zone_id = picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0

                    array_batch_temp["list_items"].append(
                        {
                            "batch_id": batch.id,
                            "id_move": move["id"],
                            "picking_id": picking_id,
                            "id_product": move["product_id"][0] if move["product_id"] else 0,
                            "product_id": [move["product_id"][0] if move["product_id"] else 0, move["product_id"][1] if len(move["product_id"]) > 1 else "N/A"],
                            "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                            "lot_id": [
                                move["lot_id"][0] if move.get("lot_id") and isinstance(move["lot_id"], (list, tuple)) and len(move["lot_id"]) > 0 else 0,
                                move["lot_id"][1] if move.get("lot_id") and isinstance(move["lot_id"], (list, tuple)) and len(move["lot_id"]) > 1 else move["lot_id"] if isinstance(move["lot_id"], str) else "N/A",
                            ],
                            "expire_date": request.env["stock.lot"].sudo().browse(move["lot_id"][0]).expiration_date if move["lot_id"] else "",
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
        
    @http.route("/api/batchs/devs", auth="user", type="json", methods=["GET"])
    def get_batches_devs(self):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # obtener la configuracion picking de la app
            config_picking = request.env["picking.config.general"].sudo().browse(1)

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            all_location_ids = list({loc_id for zone in user_wms.zone_ids.sudo().read(["location_ids"]) for loc_id in zone["location_ids"]})

            if not all_location_ids:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(request.env["stock.location"].sudo().browse(chunk).read(["id", "name", "complete_name", "priority_picking", "barcode", "priority_picking_desplay"]))

            user_location_ids = [location["id"] for location in locations]

            search_domain = [("state", "=", "in_progress"), ("picking_type_code", "=", "incoming"), ("user_id", "=", user.id)]

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
                move_unified_ids = request.env["move.line.unified"].sudo().search([("stock_picking_batch_id", "=", batch.id), ("location_id", "in", user_location_ids), ("is_done_item", "=", False)])

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

                stock_moves = move_unified_ids.read(["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty"])

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
                    "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "SIN-ZONA",
                    # "zona_entrega_tms": batch.picking_ids[0].delivery_zone_tms if batch.picking_ids and batch.picking_ids[0].delivery_zone_tms else "N/A",
                    # "order_tms": batch.picking_ids[0].order_tms if batch.picking_ids and batch.picking_ids[0].order_tms else "N/A",
                    "origin": origin_details,
                    "list_items": [],
                }

                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)}

                location_ids = {move["location_id"][0] for move in stock_moves}
                locations_dict = {loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)}

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
                                "product_id": [move["product_id"][0] if move["product_id"] else 0, move["product_id"][1] if len(move["product_id"]) > 1 else "N/A"],
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
                    picking = request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)  # Obtiene un picking asociado al batch
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener el nombre del pedido
                    picking_name = picking.display_name if picking else ""

                    # ✅ Obtener la zona de entrega del picking
                    delivery_zone_name = picking.delivery_zone_id.display_name if picking and picking.delivery_zone_id else "SIN-ZONA"
                    delivery_zone_id = picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0

                    array_batch_temp["list_items"].append(
                        {
                            "batch_id": batch.id,
                            "id_move": move["id"],
                            "picking_id": picking_id,
                            "id_product": move["product_id"][0] if move["product_id"] else 0,
                            "product_id": [move["product_id"][0] if move["product_id"] else 0, move["product_id"][1] if len(move["product_id"]) > 1 else "N/A"],
                            "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                            "lot_id": [
                                move["lot_id"][0] if move.get("lot_id") and isinstance(move["lot_id"], (list, tuple)) and len(move["lot_id"]) > 0 else 0,
                                move["lot_id"][1] if move.get("lot_id") and isinstance(move["lot_id"], (list, tuple)) and len(move["lot_id"]) > 1 else move["lot_id"] if isinstance(move["lot_id"], str) else "N/A",
                            ],
                            "expire_date": request.env["stock.lot"].sudo().browse(move["lot_id"][0]).expiration_date if move["lot_id"] else "",
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
                locations.extend(request.env["stock.location"].sudo().browse(chunk).read(["id", "name", "complete_name"]))

            if not locations:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            user_location_ids = [location["id"] for location in locations]

            # ✅ Obtener información del batch específico
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 404, "msg": "Batch no encontrado"}

            # ✅ Validar si el batch tiene movimientos unificados
            move_unified_ids = request.env["move.line.unified"].sudo().search([("stock_picking_batch_id", "=", batch.id), ("is_done_item", "=", True), ("user_operator_id", "=", user.id)])

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
                "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "SIN-ZONA",
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
                                    "product_id": [move["product_id"][0] if move["product_id"] else 0, move["product_id"][1] if len(move["product_id"]) > 1 else "N/A"],
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
                picking = request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)  # Obtiene un picking asociado al batch
                picking_id = picking.id if picking else 0

                # ✅ Obtener el nombre del pedido
                picking_name = picking.display_name if picking else ""

                # ✅ Obtener la zona de entrega del picking
                delivery_zone_name = picking.delivery_zone_id.display_name if picking and picking.delivery_zone_id else "SIN-ZONA"
                delivery_zone_id = picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0

                array_batch_temp["list_items"].append(
                    {
                        "batch_id": batch.id,
                        "id_move": move["id"],
                        "picking_id": picking_id,
                        "id_product": move["product_id"][0] if move["product_id"] else 0,
                        "product_id": [move["product_id"][0] if move["product_id"] else 0, move["product_id"][1] if len(move["product_id"]) > 1 else "N/A"],
                        "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                        "lot_id": [move["lot_id"][0] if move["lot_id"] else "", move["lot_id"][1] if move["lot_id"] else "N/A"],
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

    ## POST Transacciones enviar cantidades para valores unificados - send batch picking
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

                # ✅ Actualizar movimiento
                update_values = {"qty_done": cantidad, "new_observation": novedad, "time": formatted_time, "location_dest_id": muelle, "is_done_item": True, "date_transaction_picking": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)}

                if id_operario:
                    update_values["user_operator_id"] = id_operario

                move_unified.write(update_values)

                array_result.append({"id_move": id_move, "id_batch": id_batch, "id_product": move_unified.product_id.id, "complete": f"Se actualizó correctamente el id_move: {id_move}"})

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
            all_location_ids = list({loc_id for zone in user_wms.zone_ids.sudo().read(["location_ids"]) for loc_id in zone["location_ids"]})

            if not all_location_ids:
                return {"code": 400, "msg": "El usuario no tiene ubicaciones asociadas"}

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(request.env["stock.location"].sudo().browse(chunk).read(["id", "name", "complete_name", "priority_picking", "barcode", "priority_picking_desplay"]))

            user_location_ids = [location["id"] for location in locations]

            state_batch = ["done", "in_progress"]

            fecha_inicio = datetime.strptime(fecha_batch + " 00:00:00", "%Y-%m-%d %H:%M:%S")
            fecha_fin = datetime.strptime(fecha_batch + " 23:59:59", "%Y-%m-%d %H:%M:%S")

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search([("state", "in", state_batch), ("picking_type_code", "=", "internal"), ("write_date", ">=", fecha_inicio), ("write_date", "<=", fecha_fin)])

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos unificados
                move_unified_ids = request.env["move.line.unified"].sudo().search([("stock_picking_batch_id", "=", batch.id), ("is_done_item", "=", True), ("user_operator_id", "=", user.id)])

                if not move_unified_ids:
                    continue

                stock_moves = move_unified_ids.read(["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty", "qty_done"])

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
