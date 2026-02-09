# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from datetime import datetime, timedelta
import pytz
import base64


class TransaccionDataPacking(http.Controller):

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

    ## GET Transacciones batchs para packing
    @http.route("/api/batch_packing", auth="user", type="json", methods=["GET"])
    def get_batch_packing(self, **kwargs):
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
            if not user:
                return {"code": 401, "update_version": update_required, "msg": "Usuario no autenticado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_batch = []

            base_url = request.httprequest.host_url.rstrip("/")

            # # ✅ Verificar si el usuario tiene almacenes permitidos
            # allowed_warehouses = user.allowed_warehouse_ids
            # if not allowed_warehouses:
            #     return {"code": 400, "msg": "El usuario no tiene acceso a ningún almacén"}

            # obtener la configuracion picking de la app
            config_picking = request.env["packing.config.general"].sudo().browse(1)

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # ✅ Obtener la estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Iterar sobre los almacenes permitidos y procesar cada uno
            for warehouse in allowed_warehouses:

                # Obtener el campo `delivery_steps` del almacén
                delivery_steps = warehouse.delivery_steps
                if not delivery_steps:
                    continue  # Saltar si no hay información sobre `delivery_steps`

                # Determinar el `sequence_code` basado en los pasos de entrega
                if delivery_steps == "ship_only":
                    # 1 paso: Entregar bienes directamente
                    sequence_code = "OUT"
                elif delivery_steps == "pick_ship":
                    # 2 pasos: Enviar bienes a ubicación de salida y entregar
                    sequence_code = "OUT"
                elif delivery_steps == "pick_pack_ship":
                    # 3 pasos: Empaquetar, transferir bienes a ubicación de salida, y enviar
                    sequence_code = "PACK"
                else:
                    continue  # Si no hay una coincidencia válida, saltar este almacén

                # search_domain = [
                #     ("state", "=", "in_progress"),
                #     ("picking_type_id.sequence_code", "=", sequence_code),
                #     ("picking_type_id.warehouse_id", "=", warehouse.id),  # Filtro por almacén actual
                #     ("button_get_value_batch_pack", "=", False),
                #     ("button_is_set_value_batch_pack_done", "=", False),
                # ]
                search_domain = [
                    ("state", "=", "in_progress"),
                    ("picking_type_id.sequence_code", "=", sequence_code),
                    ("picking_type_id.warehouse_id", "=", warehouse.id),
                ]

                # Validar si los campos existen en el modelo antes de agregarlos al dominio
                batch_model = request.env["stock.picking.batch"].sudo()
                available_fields = batch_model.fields_get().keys()

                if "button_get_value_batch_pack" in available_fields:
                    search_domain.append(("button_get_value_batch_pack", "=", False))

                if "button_is_set_value_batch_pack_done" in available_fields:
                    search_domain.append(("button_is_set_value_batch_pack_done", "=", False))

                if config_picking.packing_type == "responsible":
                    search_domain.append(("user_id", "in", [user.id, False]))

                # ✅ Buscar lotes en progreso con secuencia "OUT"
                batches = request.env["stock.picking.batch"].sudo().search(search_domain)

                for batch in batches:
                    if batch.move_line_ids:
                        user_info = {
                            "user_id": batch.user_id.id if batch.user_id else 0,
                            "user_name": batch.user_id.name if batch.user_id else "Desconocido",
                        }

                        manejo_temperatura = False

                        productos_con_temperatura = batch.move_line_ids.mapped("product_id").filtered(lambda p: hasattr(p, "temperature_control") and p.temperature_control)
                        if productos_con_temperatura:
                            manejo_temperatura = True

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

                        array_batch_temp = {
                            "id": batch.id,
                            "name": batch.name,
                            "scheduleddate": batch.scheduled_date,
                            "state": batch.state,
                            "user_id": user_info["user_id"] if batch.user_id else 0,
                            "user_name": user_info["user_name"] if batch.user_id else "",
                            "order_by": picking_strategy.picking_priority_app if picking_strategy else "",
                            "order_picking": picking_strategy.picking_order_app if picking_strategy else "",
                            "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                            "cantidad_pedidos": 0,
                            "start_time_pack": batch.start_time_pack or "",
                            "end_time_pack": batch.end_time_pack or "",
                            "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "N/A",
                            # "zona_entrega_tms": batch.picking_ids[0].delivery_zone_tms if batch.picking_ids and batch.picking_ids[0].delivery_zone_tms else "N/A",
                            "zona_entrega_tms": "",
                            # "order_tms": batch.picking_ids[0].order_tms if batch.picking_ids and batch.picking_ids[0].order_tms else "N/A",
                            "maneja_temperatura": manejo_temperatura,
                            "temperatura": batch.temperature_batch if hasattr(batch, "temperature_batch") else "",
                            "order_tms": "",
                            "origin": origin_details,
                            "lista_pedidos": [],
                        }

                        valid_pickings_found = False

                        for picking in batch.picking_ids:
                            pedido = {
                                "id": picking.id,
                                "batch_id": batch.id,
                                "name": picking.name,
                                "referencia": picking.origin if picking.origin else "",
                                "contacto": picking.partner_id.id if picking.partner_id else 0,
                                "contacto_name": picking.partner_id.name if picking.partner_id else "N/A",
                                "tipo_operacion": picking.picking_type_id.name if picking.picking_type_id else "N/A",
                                "cantidad_productos": len(picking.move_line_ids.filtered(lambda ml: not ml.is_done_item_pack)),
                                "cantidad_productos_total": len(picking.move_line_ids),
                                "zona_entrega": picking.delivery_zone_id.name if picking.delivery_zone_id else "",
                                # "zona_entrega_tms": picking.delivery_zone_tms if picking.delivery_zone_tms else "",
                                # "order_tms": picking.order_tms if picking.order_tms else "",
                                "zona_entrega_tms": "",
                                "order_tms": "",
                                "numero_paquetes": len(picking.move_line_ids.mapped("package_id")),
                                "lista_productos": [],
                                "lista_paquetes": [],
                            }

                            # ✅ Procesar líneas de movimiento
                            for move_line in picking.move_line_ids:
                                location = move_line.location_id
                                location_dest = move_line.location_dest_id

                                product = move_line.product_id
                                lot = move_line.lot_id

                                # ✅ Verificar dinámicamente la existencia de `barcode_ids`
                                array_all_barcode = []
                                if "barcode_ids" in product.fields_get():
                                    array_all_barcode = [
                                        {
                                            "barcode": barcode.name,
                                            "batch_id": batch.id,
                                            "id_move": move_line.id,
                                            "id_product": product.id,
                                            "cantidad": 1,
                                            "product_id": product.id,
                                        }
                                        for barcode in product.barcode_ids
                                        if barcode.name  # Filtra solo los barcodes válidos
                                    ]

                                # ✅ Obtener empaques del producto
                                array_packing = (
                                    [
                                        {
                                            "barcode": pack.barcode,
                                            "cantidad": pack.qty,
                                            "batch_id": batch.id,
                                            "id_move": move_line.id,
                                            "id_product": product.id,
                                        }
                                        for pack in product.packaging_ids
                                        if pack.barcode  # Incluye solo si barcode es válido
                                    ]
                                    if product.packaging_ids
                                    else []
                                )

                                if move_line.is_done_item_pack == False:
                                    productos = {
                                        "id_move": move_line.id,
                                        "product_id": [product.id, product.display_name],
                                        "batch_id": batch.id,
                                        "pedido_id": picking.id,
                                        "id_product": product.id if product else 0,
                                        "product_code": product.default_code if product else "",
                                        "picking_id": picking.id,
                                        "lote_id": lot.id if lot else "",
                                        "lot_id": [lot.id, lot.name if lot else ""] if lot else [],
                                        "expire_date": lot.expiration_date or "",
                                        "location_id": [location.id, location.display_name if location else ""],
                                        "barcode_location": location.barcode if location else "",
                                        "location_dest_id": [location_dest.id, location_dest.name if location_dest else ""],
                                        "barcode_location_dest": location_dest.barcode if location_dest else "",
                                        "other_barcode": array_all_barcode,
                                        "quantity": move_line.quantity,
                                        "tracking": product.tracking if product else "",
                                        "barcode": product.barcode if product else "",
                                        "product_packing": array_packing,
                                        "weight": product.weight if product else 0,
                                        "unidades": product.uom_id.name if product.uom_id else "UND",
                                        # "rimoval_priority": location.priority_picking,
                                        "rimoval_priority": location.priority_picking_desplay if location else 0,
                                        "maneja_temperatura": product.temperature_control if hasattr(product, "temperature_control") else False,
                                        "temperatura": move_line.temperature if hasattr(move_line, "temperature") else 0,
                                        # "imagen": move_line.imagen if (hasattr(move_line, "imagen") and move_line.imagen) else "",
                                    }

                                    pedido["lista_productos"].append(productos)

                            # ✅ Procesar paquetes con productos empaquetados (is_done_item_pack == True)
                            move_lines_in_picking = picking.move_line_ids.filtered(lambda ml: ml.package_id or ml.result_package_id)
                            unique_packages = move_lines_in_picking.mapped("package_id") + move_lines_in_picking.mapped("result_package_id")

                            for pack in unique_packages:
                                move_lines_in_package = move_lines_in_picking.filtered(lambda ml: (ml.package_id == pack or ml.result_package_id == pack) and ml.is_done_item_pack)

                                cantidad_productos = len(move_lines_in_package)

                                line_with_image = move_lines_in_package.filtered(lambda ml: getattr(ml, "imagen", False))[:1]
                                line_with_observation = move_lines_in_package.filtered(lambda ml: getattr(ml, "imagen_observation", False))[:1]

                                # Generar URLs de imágenes solo si existen líneas con imágenes
                                image_url = ""
                                image_novedad_url = ""

                                if line_with_image:
                                    image_url = f"{base_url}/api/view_imagen_linea_recepcion/{line_with_image.id}"

                                if line_with_observation:
                                    image_novedad_url = f"{base_url}/api/view_imagen_observation/{line_with_observation.id}"

                                package = {
                                    "name": pack.name,
                                    "id": pack.id,
                                    "batch_id": batch.id,
                                    "pedido_id": picking.id,
                                    "cantidad_productos": cantidad_productos,
                                    "lista_productos_in_packing": [],
                                    "is_sticker": pack.is_sticker,
                                    "is_certificate": pack.is_certificate,
                                    "fecha_creacion": pack.create_date.strftime("%Y-%m-%d") if pack.create_date else "",
                                    "fecha_actualizacion": pack.write_date.strftime("%Y-%m-%d") if pack.write_date else "",
                                    "consecutivo": getattr(move_lines_in_package[0], "faber_box_number", "") if move_lines_in_package else "",
                                }
                                pedido["lista_paquetes"].append(package)

                                for move_line in move_lines_in_package:
                                    product = move_line.product_id
                                    lot = move_line.lot_id

                                    product_in_packing = {
                                        "id_move": move_line.id,
                                        "pedido_id": picking.id,
                                        "batch_id": batch.id,
                                        "package_name": pack.name,
                                        "quantity_separate": move_line.quantity,
                                        "id_product": product.id if product else 0,
                                        "product_id": [product.id, product.display_name],
                                        "name_packing": pack.name,
                                        "cantidad_enviada": move_line.quantity,
                                        "unidades": product.uom_id.name if product.uom_id else "UND",
                                        "peso": product.weight if product else 0,
                                        "lote_id": [lot.id, lot.name if lot else ""] if lot else [],
                                        "observation": move_line.new_observation_packing,
                                        "weight": product.weight if product else 0,
                                        "is_sticker": pack.is_sticker,
                                        "is_certificate": pack.is_certificate,
                                        "id_package": pack.id,
                                        "quantity": move_line.quantity,
                                        "tracking": product.tracking if product else "",
                                        "maneja_temperatura": product.temperature_control if hasattr(product, "temperature_control") else False,
                                        "temperatura": move_line.temperature if hasattr(move_line, "temperature") else 0,
                                        "image": f"{base_url}/api/view_imagen_linea_recepcion/{move_line.id}" if getattr(move_line, "imagen", False) else "",
                                        "image_novedad": f"{base_url}/api/view_imagen_observation/{move_line.id}" if getattr(move_line, "imagen_observation", False) else "",
                                        "time_separate": move_line.time_packing if move_line.time_packing else 0,
                                        "package_consecutivo": move_line.faber_box_number if hasattr(move_line, "faber_box_number") else "",
                                    }

                                    package["lista_productos_in_packing"].append(product_in_packing)
                            if pedido["lista_productos"]:
                                array_batch_temp["lista_pedidos"].append(pedido)
                                valid_pickings_found = True

                            # Solo añadir el batch al array final si tiene pedidos válidos
                        if valid_pickings_found:
                            array_batch_temp["cantidad_pedidos"] = len(array_batch_temp["lista_pedidos"])
                            array_batch.append(array_batch_temp)

            return {"code": 200, "update_version": update_required, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "update_version": update_required, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "update_version": update_required, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/batch_packing/v2", auth="user", type="json", methods=["GET"])
    def get_batch_packing_v2(self, **kwargs):
        try:
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_batch = []

            base_url = request.httprequest.host_url.rstrip("/")

            # # ✅ Verificar si el usuario tiene almacenes permitidos
            # allowed_warehouses = user.allowed_warehouse_ids
            # if not allowed_warehouses:
            #     return {"code": 400, "msg": "El usuario no tiene acceso a ningún almacén"}

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # ✅ Obtener la estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Iterar sobre los almacenes permitidos y procesar cada uno
            for warehouse in allowed_warehouses:

                # Obtener el campo `delivery_steps` del almacén
                delivery_steps = warehouse.delivery_steps
                if not delivery_steps:
                    continue  # Saltar si no hay información sobre `delivery_steps`

                # Determinar el `sequence_code` basado en los pasos de entrega
                if delivery_steps == "ship_only":
                    # 1 paso: Entregar bienes directamente
                    sequence_code = "OUT"
                elif delivery_steps == "pick_ship":
                    # 2 pasos: Enviar bienes a ubicación de salida y entregar
                    sequence_code = "OUT"
                elif delivery_steps == "pick_pack_ship":
                    # 3 pasos: Empaquetar, transferir bienes a ubicación de salida, y enviar
                    sequence_code = "PACK"
                else:
                    continue  # Si no hay una coincidencia válida, saltar este almacén

                # ✅ Buscar lotes en progreso con secuencia "OUT"
                batches = (
                    request.env["stock.picking.batch"]
                    .sudo()
                    .search(
                        [
                            ("state", "=", "in_progress"),
                            ("picking_type_id.sequence_code", "=", sequence_code),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),  # Filtro por almacén actual
                        ]
                    )
                )

                for batch in batches:
                    if batch.move_line_ids:
                        user_info = {
                            "user_id": batch.user_id.id if batch.user_id else 0,
                            "user_name": batch.user_id.name if batch.user_id else "Desconocido",
                        }

                        manejo_temperatura = False

                        productos_con_temperatura = batch.move_line_ids.mapped("product_id").filtered(lambda p: hasattr(p, "temperature_control") and p.temperature_control)
                        if productos_con_temperatura:
                            manejo_temperatura = True

                        array_batch_temp = {
                            "id": batch.id,
                            "name": batch.name,
                            "scheduleddate": batch.scheduled_date,
                            "state": batch.state,
                            "user_id": user_info["user_id"] if batch.user_id else 0,
                            "user_name": user_info["user_name"] if batch.user_id else "",
                            "order_by": picking_strategy.picking_priority_app if picking_strategy else "",
                            "order_picking": picking_strategy.picking_order_app if picking_strategy else "",
                            "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                            "cantidad_pedidos": 0,
                            "start_time_pack": batch.start_time_pack or "",
                            "end_time_pack": batch.end_time_pack or "",
                            "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "N/A",
                            # "zona_entrega_tms": batch.picking_ids[0].delivery_zone_tms if batch.picking_ids and batch.picking_ids[0].delivery_zone_tms else "N/A",
                            "zona_entrega_tms": "",
                            # "order_tms": batch.picking_ids[0].order_tms if batch.picking_ids and batch.picking_ids[0].order_tms else "N/A",
                            "maneja_temperatura": manejo_temperatura,
                            "temperatura": batch.temperature_batch if hasattr(batch, "temperature_batch") else "",
                            "order_tms": "",
                            "lista_pedidos": [],
                        }

                        valid_pickings_found = False

                        for picking in batch.picking_ids:
                            pedido = {
                                "id": picking.id,
                                "batch_id": batch.id,
                                "name": picking.name,
                                "referencia": picking.origin if picking.origin else "",
                                "contacto": picking.partner_id.id if picking.partner_id else 0,
                                "contacto_name": picking.partner_id.name if picking.partner_id else "N/A",
                                "tipo_operacion": picking.picking_type_id.name if picking.picking_type_id else "N/A",
                                "cantidad_productos": len(picking.move_line_ids.filtered(lambda ml: not ml.is_done_item_pack)),
                                "cantidad_productos_total": len(picking.move_line_ids),
                                "zona_entrega": picking.delivery_zone_id.name if picking.delivery_zone_id else "",
                                # "zona_entrega_tms": picking.delivery_zone_tms if picking.delivery_zone_tms else "",
                                # "order_tms": picking.order_tms if picking.order_tms else "",
                                "zona_entrega_tms": "",
                                "order_tms": "",
                                "numero_paquetes": len(picking.move_line_ids.mapped("package_id")),
                                "lista_productos": [],
                                "lista_paquetes": [],
                            }

                            # ✅ Procesar líneas de movimiento
                            for move_line in picking.move_line_ids:
                                location = move_line.location_id
                                location_dest = move_line.location_dest_id

                                product = move_line.product_id
                                lot = move_line.lot_id

                                # ✅ Verificar dinámicamente la existencia de `barcode_ids`
                                array_all_barcode = []
                                if "barcode_ids" in product.fields_get():
                                    array_all_barcode = [
                                        {
                                            "barcode": barcode.name,
                                            "batch_id": batch.id,
                                            "id_move": move_line.id,
                                            "id_product": product.id,
                                            "cantidad": 1,
                                            "product_id": product.id,
                                        }
                                        for barcode in product.barcode_ids
                                        if barcode.name  # Filtra solo los barcodes válidos
                                    ]

                                # ✅ Obtener empaques del producto
                                array_packing = (
                                    [
                                        {
                                            "barcode": pack.barcode,
                                            "cantidad": pack.qty,
                                            "batch_id": batch.id,
                                            "id_move": move_line.id,
                                            "id_product": product.id,
                                        }
                                        for pack in product.packaging_ids
                                        if pack.barcode  # Incluye solo si barcode es válido
                                    ]
                                    if product.packaging_ids
                                    else []
                                )

                                if move_line.is_done_item_pack == False:
                                    productos = {
                                        "id_move": move_line.id,
                                        "product_id": [product.id, product.display_name],
                                        "batch_id": batch.id,
                                        "pedido_id": picking.id,
                                        "id_product": product.id if product else 0,
                                        "picking_id": picking.id,
                                        "lote_id": lot.id if lot else "",
                                        "lot_id": [lot.id, lot.name if lot else ""] if lot else [],
                                        "expire_date": lot.expiration_date or "",
                                        "location_id": [location.id, location.display_name if location else ""],
                                        "barcode_location": location.barcode if location else "",
                                        "location_dest_id": [location_dest.id, location_dest.name if location_dest else ""],
                                        "barcode_location_dest": location_dest.barcode if location_dest else "",
                                        "other_barcode": array_all_barcode,
                                        "quantity": move_line.quantity,
                                        "tracking": product.tracking if product else "",
                                        "barcode": product.barcode if product else "",
                                        "product_packing": array_packing,
                                        "weight": product.weight if product else 0,
                                        "unidades": product.uom_id.name if product.uom_id else "UND",
                                        # "rimoval_priority": location.priority_picking,
                                        "rimoval_priority": location.priority_picking_desplay if location else 0,
                                        "maneja_temperatura": product.temperature_control if hasattr(product, "temperature_control") else False,
                                        "temperatura": move_line.temperature if hasattr(move_line, "temperature") else 0,
                                        # "imagen": move_line.imagen if (hasattr(move_line, "imagen") and move_line.imagen) else "",
                                    }

                                    pedido["lista_productos"].append(productos)

                            # ✅ Procesar paquetes con productos empaquetados (is_done_item_pack == True)
                            move_lines_in_picking = picking.move_line_ids.filtered(lambda ml: ml.package_id or ml.result_package_id)
                            unique_packages = move_lines_in_picking.mapped("package_id") + move_lines_in_picking.mapped("result_package_id")

                            for pack in unique_packages:
                                move_lines_in_package = move_lines_in_picking.filtered(lambda ml: (ml.package_id == pack or ml.result_package_id == pack) and ml.is_done_item_pack)

                                cantidad_productos = len(move_lines_in_package)

                                line_with_image = move_lines_in_package.filtered(lambda ml: getattr(ml, "imagen", False))[:1]
                                line_with_observation = move_lines_in_package.filtered(lambda ml: getattr(ml, "imagen_observation", False))[:1]

                                # Generar URLs de imágenes solo si existen líneas con imágenes
                                image_url = ""
                                image_novedad_url = ""

                                if line_with_image:
                                    image_url = f"{base_url}/api/view_imagen_linea_recepcion/{line_with_image.id}"

                                if line_with_observation:
                                    image_novedad_url = f"{base_url}/api/view_imagen_observation/{line_with_observation.id}"

                                package = {
                                    "name": pack.name,
                                    "id": pack.id,
                                    "batch_id": batch.id,
                                    "pedido_id": picking.id,
                                    "cantidad_productos": cantidad_productos,
                                    "lista_productos_in_packing": [],
                                    "is_sticker": pack.is_sticker,
                                    "is_certificate": pack.is_certificate,
                                    "fecha_creacion": pack.create_date.strftime("%Y-%m-%d") if pack.create_date else "",
                                    "fecha_actualizacion": pack.write_date.strftime("%Y-%m-%d") if pack.write_date else "",
                                    "consecutivo": getattr(move_lines_in_package[0], "faber_box_number", "") if move_lines_in_package else "",
                                }
                                pedido["lista_paquetes"].append(package)

                                for move_line in move_lines_in_package:
                                    product = move_line.product_id
                                    lot = move_line.lot_id

                                    product_in_packing = {
                                        "id_move": move_line.id,
                                        "pedido_id": picking.id,
                                        "batch_id": batch.id,
                                        "package_name": pack.name,
                                        "quantity_separate": move_line.quantity,
                                        "id_product": product.id if product else 0,
                                        "product_id": [product.id, product.display_name],
                                        "name_packing": pack.name,
                                        "cantidad_enviada": move_line.quantity,
                                        "unidades": product.uom_id.name if product.uom_id else "UND",
                                        "peso": product.weight if product else 0,
                                        "lote_id": [lot.id, lot.name if lot else ""] if lot else [],
                                        "observation": move_line.new_observation_packing,
                                        "weight": product.weight if product else 0,
                                        "is_sticker": pack.is_sticker,
                                        "is_certificate": pack.is_certificate,
                                        "id_package": pack.id,
                                        "quantity": move_line.quantity,
                                        "tracking": product.tracking if product else "",
                                        "maneja_temperatura": product.temperature_control if hasattr(product, "temperature_control") else False,
                                        "temperatura": move_line.temperature if hasattr(move_line, "temperature") else 0,
                                        "image": f"{base_url}/api/view_imagen_linea_recepcion/{move_line.id}" if getattr(move_line, "imagen", False) else "",
                                        "image_novedad": f"{base_url}/api/view_imagen_observation/{move_line.id}" if getattr(move_line, "imagen_observation", False) else "",
                                        "time_separate": move_line.time_packing if move_line.time_packing else 0,
                                        "package_consecutivo": move_line.faber_box_number if hasattr(move_line, "faber_box_number") else "",
                                    }

                                    package["lista_productos_in_packing"].append(product_in_packing)
                            if pedido["lista_productos"]:
                                array_batch_temp["lista_pedidos"].append(pedido)
                                valid_pickings_found = True

                            # Solo añadir el batch al array final si tiene pedidos válidos
                        if valid_pickings_found:
                            array_batch_temp["cantidad_pedidos"] = len(array_batch_temp["lista_pedidos"])
                            array_batch.append(array_batch_temp)

            return {"code": 200, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Transacciones crear paquete para packing
    @http.route("/api/create_package", auth="user", type="json", methods=["POST"])
    def create_packaging(self):
        try:
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            # ✅ Crear un paquete en 'stock.quant.package'
            new_package = request.env["stock.quant.package"].sudo().create({})

            if not new_package:
                return {"code": 400, "msg": "No se pudo crear el paquete"}

            # ✅ Leer el paquete recién creado
            packaging = {
                "id": new_package.id,
                "name": new_package.name,
                "create_date": (new_package.create_date.strftime("%Y-%m-%d %H:%M:%S") if new_package.create_date else ""),
                "write_date": (new_package.write_date.strftime("%Y-%m-%d %H:%M:%S") if new_package.write_date else ""),
            }

            return {
                "code": 200,
                "msg": "Paquete creado correctamente",
                "packaging": packaging,
            }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except ValidationError as e:
            return {"code": 400, "msg": f"Error de validación: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/send_packing", auth="user", type="json", methods=["POST"])
    def send_packing(self, **auth):
        try:
            # ✅ Validar autenticación
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            id_batch = auth.get("id_batch")
            list_item = auth.get("list_item", [])
            is_sticker = auth.get("is_sticker", False)
            is_certificate = auth.get("is_certificate", False)
            peso_total_paquete = auth.get("peso_total_paquete", 0)

            array_msg = []
            nuevas_lineas_creadas = []

            # ✅ Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            # ✅ Crear el paquete manualmente
            pack = (
                request.env["stock.quant.package"]
                .sudo()
                .create(
                    {
                        "is_sticker": is_sticker,
                        "is_certificate": is_certificate,
                    }
                )
            )

            pickings_procesados = set()

            for move in list_item:
                product_id = move.get("product_id")
                location_id = move.get("location_id")
                lote = move.get("lote", None)
                cantidad_separada = move.get("cantidad_separada", 0)
                id_move = move.get("id_move")
                observacion = move.get("observacion", "")
                id_operario = move.get("id_operario", 0)
                fecha_transaccion = move.get("fecha_transaccion", "")
                time = move.get("time_line", 0)

                # poner la observacion en minuscula

                move_line = request.env["stock.move.line"].sudo().browse(id_move)

                if move_line.exists():
                    if move_line.quantity >= cantidad_separada:
                        pickings_procesados.add(move_line.picking_id)

                        if observacion.lower() != "sin novedad":
                            move_line.write(
                                {
                                    "result_package_id": pack.id,
                                    "quantity": cantidad_separada,
                                    "new_observation_packing": observacion,
                                    "user_operator_id": id_operario,
                                    "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                                    "is_done_item_pack": True,
                                    "time_packing": time,
                                }
                            )
                        if cantidad_separada < move_line.quantity:
                            cantidad_original = move_line.quantity

                            # ✅ 1. Restar a la original
                            move_line.write({"quantity": cantidad_original - cantidad_separada})

                            # ✅ 2. Copiar la línea original
                            new_line_vals = move_line.copy_data()[0]

                            # ✅ 3. Actualizar los datos ANTES de crearla
                            new_line_vals.update(
                                {
                                    "quantity": cantidad_separada,
                                    "result_package_id": pack.id,
                                    "new_observation_packing": observacion,
                                    "user_operator_id": id_operario,
                                    "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                                    "is_done_item_pack": True,
                                    "time_packing": time,
                                }
                            )

                            # ✅ 4. Crear la línea nueva con los valores actualizados
                            new_line = request.env["stock.move.line"].sudo().create(new_line_vals)

                            new_line.write({"is_done_item_pack": True})

                            nuevas_lineas_creadas.append(
                                {
                                    "id_move_original": id_move,
                                    "id_move_procesada": new_line.id,
                                    "cantidad_procesada": cantidad_separada,
                                    "cantidad_restante": cantidad_original - cantidad_separada,
                                    "new_line_obj": new_line,  # Para obtener consecutivo después
                                }
                            )

                        else:
                            # ✅ Asignar directamente al paquete si no hay división
                            move_line.write(
                                {
                                    "result_package_id": pack.id,
                                    "quantity": cantidad_separada,
                                    "new_observation_packing": observacion,
                                    "user_operator_id": id_operario,
                                    "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                                    "is_done_item_pack": True,
                                    "time_packing": time,
                                }
                            )
                    elif cantidad_separada == move_line.quantity:
                        # ✅ Asignar directamente al paquete si la cantidad es igual
                        move_line.write(
                            {
                                "result_package_id": pack.id,
                                "quantity": cantidad_separada,
                                "new_observation_packing": observacion,
                                "user_operator_id": id_operario,
                                "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                                "is_done_item_pack": True,
                                "time_packing": time,
                            }
                        )
                    else:
                        array_msg.append(
                            {
                                "code": 400,
                                "msg": f"La cantidad separada {cantidad_separada} es mayor a la cantidad disponible {move_line.quantity}",
                            }
                        )
                        continue
                else:
                    array_msg.append(
                        {
                            "code": 400,
                            "msg": f"Error al actualizar el paquete en stock.move.line {id_move}",
                        }
                    )

            # ✅ CORREGIDO: Generar números de caja para TODOS los pickings únicos procesados
            batch.action_generate_box_numbers()

            # ✅ CORREGIDO: Obtener el consecutivo del primer picking que contenga líneas del paquete
            consecutivo = "Caja1"  # valor por defecto
            primera_linea_del_paquete = None

            for picking in pickings_procesados:
                lineas_del_paquete = picking.move_line_ids.filtered(lambda l: l.result_package_id and l.result_package_id.id == pack.id)
                if lineas_del_paquete:
                    primera_linea_del_paquete = lineas_del_paquete[0]
                    consecutivo = primera_linea_del_paquete.faber_box_number or "Caja1"
                    break  # Tomar el consecutivo del primer picking que tenga líneas del paquete

            # ✅ NUEVO: Actualizar consecutivo en las nuevas líneas creadas
            for nueva_linea in nuevas_lineas_creadas:
                if "new_line_obj" in nueva_linea:
                    line_obj = nueva_linea["new_line_obj"]
                    nueva_linea["consecutivo"] = line_obj.faber_box_number or consecutivo
                    # Remover el objeto de la respuesta
                    del nueva_linea["new_line_obj"]

            array_msg.append(
                {
                    "id_paquete": pack.id,
                    "name_paquete": pack.name,
                    "id_batch": batch.id,
                    "cantidad_productos_en_el_paquete": len(list_item),
                    "is_sticker": is_sticker,
                    "is_certificate": is_certificate,
                    "peso": peso_total_paquete,
                    "consecutivo": consecutivo,  # ✅ NUEVO: Consecutivo correcto
                    "list_item": list_item,
                }
            )

            return {"code": 200, "result": array_msg}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except ValidationError as e:
            return {"code": 400, "msg": f"Error de validación: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ### POST Transacciones para desempacar paquete en packing
    @http.route("/api/unpacking", auth="user", type="json", methods=["POST"])
    def unpacking(self, **auth):
        try:
            # ✅ Validar autenticación
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            id_batch = auth.get("id_batch")
            id_paquete = auth.get("id_paquete")
            list_item = auth.get("list_item", [])

            array_msg = []

            # ✅ Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            # ✅ Validar si el paquete existe
            paquete = request.env["stock.quant.package"].sudo().browse(id_paquete)
            if not paquete.exists():
                return {
                    "code": 400,
                    "msg": f"El paquete {id_paquete} no existe",
                }

            # ✅ Procesar cada ítem en la lista
            for move in list_item:
                product_id = move.get("product_id")
                location_id = move.get("location_id")
                lote = move.get("lote", None)
                cantidad_separada = move.get("cantidad_separada", 0)
                id_move = move.get("id_move")
                observacion = move.get("observacion", "")
                id_operario = move.get("id_operario", 0)

                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                ]

                if lote:
                    domain.append(("lot_id", "=", int(lote)))

                # ✅ Actualizar stock.move.line con el paquete
                move_line = request.env["stock.move.line"].sudo().browse(id_move)

                if move_line.exists():
                    move_line.write(
                        {
                            "result_package_id": False,
                            "new_observation_packing": observacion,
                            "user_operator_id": id_operario,
                            "date_transaction_packing": "",
                            "is_done_item_pack": False,
                        }
                    )

                    array_msg.append(
                        {
                            "id_paquete": paquete.id,
                            "name_paquete": paquete.name,
                            "id_batch": batch.id,
                            "cantidad_productos_en_el_paquete": len(list_item),
                            "list_item": list_item,
                        }
                    )
                else:
                    array_msg.append(
                        {
                            "code": 400,
                            "msg": f"Error al actualizar el paquete en stock.move.line {id_move}",
                        }
                    )

            if not batch.move_line_ids.filtered(lambda ml: ml.result_package_id):
                paquete.unlink()
                array_msg.append({"code": 200, "msg": f"El paquete {id_paquete} ha sido eliminado"})

            return {"code": 200, "result": array_msg}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except ValidationError as e:
            return {"code": 400, "msg": f"Error de validación: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/send_temperatura/batch", auth="user", type="json", methods=["POST"])
    def send_temperatura_batch(self, **auth):
        try:
            # ✅ Validar autenticación
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            id_batch = auth.get("id_batch")
            temperatura = auth.get("temperatura", 0)

            # ✅ Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            # ✅ Actualizar la temperatura del lote
            batch.write({"temperature_batch": temperatura})

            return {"code": 200, "msg": "Temperatura actualizada correctamente"}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/send_image_linea_recepcion/batch", auth="user", type="http", methods=["POST"], csrf=False)
    def send_image_linea_recepcion_batch(self, **post):
        try:
            user = request.env.user
            if not user:
                return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

            show_photo_temperature = request.env["appwms.temperature"].sudo().search([], limit=1)
            show_photo_required = show_photo_temperature.show_photo_temperature if show_photo_temperature else False

            id_linea_recepcion = post.get("move_line_id")
            image_file = request.httprequest.files.get("image_data")
            temperatura = post.get("temperatura", 0.0)

            # Validar ID de línea de recepción
            if not id_linea_recepcion:
                return request.make_json_response({"code": 400, "msg": "ID de línea de recepción no válido"})

            # Validar archivo de imagen SOLO si show_photo_temperature es True
            if show_photo_required and not image_file:
                return request.make_json_response({"code": 400, "msg": "No se recibió ningún archivo de imagen"})

            # Convertir ID a entero si viene como string
            try:
                id_linea_recepcion = int(id_linea_recepcion)
            except (ValueError, TypeError):
                return request.make_json_response({"code": 400, "msg": "ID de línea de recepción debe ser un número"})

            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", id_linea_recepcion)], limit=1)

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
                    return request.make_json_response({"code": 400, "msg": f"Formato de imagen no permitido. Formatos válidos: {', '.join(allowed_extensions)}"})

                # Validar tamaño del archivo (máximo 5MB)
                max_size = 5 * 1024 * 1024
                image_file.seek(0, 2)
                file_size = image_file.tell()
                image_file.seek(0)

                if file_size > max_size:
                    return request.make_json_response({"code": 400, "msg": "El archivo es demasiado grande. Tamaño máximo: 5MB"})

                # Leer el contenido del archivo y codificarlo a base64
                image_data_bytes = image_file.read()
                image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

                # Información de la imagen para la respuesta
                image_info = {
                    "filename": image_file.filename,
                    "image_size": len(image_data_bytes),
                    "image_url": f"{request.httprequest.host_url.rstrip('/')}/api/view_imagen_linea_recepcion/batch/{id_linea_recepcion}",
                    "json_url": f"{request.httprequest.host_url.rstrip('/')}/api/get_imagen_linea_recepcion/batch/{id_linea_recepcion}",
                }

            # Actualizar la línea de recepción
            update_data = {"temperature": temperatura}
            if image_data_base64:
                update_data["imagen"] = image_data_base64

            linea_recepcion.sudo().write(update_data)

            # Preparar respuesta
            response_data = {
                "code": 200,
                "result": "Datos guardados correctamente en la línea del batch",
                "line_id": id_linea_recepcion,
                "temperature": temperatura,
                "show_photo_temperature": show_photo_required,
                "batch_type": "image_recepcion",
                "image_processed": bool(image_file),
            }

            # Agregar información de imagen si se procesó
            if image_info:
                response_data.update(image_info)

            return request.make_json_response(response_data)

        except Exception as e:
            return request.make_json_response({"code": 500, "msg": "Error interno del servidor"})

    @http.route("/api/send_imagen_observation/batch", auth="user", type="http", methods=["POST"], csrf=False)
    def send_imagen_observation_batch(self, **post):
        try:
            user = request.env.user
            if not user:
                return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

            id_linea_recepcion = post.get("move_line_id")
            image_file = request.httprequest.files.get("image_data")

            # Validar ID de línea de recepción
            if not id_linea_recepcion:
                return request.make_json_response({"code": 400, "msg": "ID de línea de recepción no válido"})

            # Validar archivo de imagen
            if not image_file:
                return request.make_json_response({"code": 400, "msg": "No se recibió ningún archivo de imagen"})

            # Convertir ID a entero si viene como string
            try:
                id_linea_recepcion = int(id_linea_recepcion)
            except (ValueError, TypeError):
                return request.make_json_response({"code": 400, "msg": "ID de línea de recepción debe ser un número"})

            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", id_linea_recepcion)], limit=1)

            if not linea_recepcion:
                return request.make_json_response({"code": 404, "msg": "Línea de recepción no encontrada"})

            # Validar tipo de archivo
            allowed_extensions = ["jpg", "jpeg", "png", "gif", "bmp", "webp"]
            file_extension = image_file.filename.lower().split(".")[-1] if image_file.filename else ""
            if file_extension not in allowed_extensions:
                return request.make_json_response({"code": 400, "msg": f"Formato de imagen no permitido. Formatos válidos: {', '.join(allowed_extensions)}"})

            # Validar tamaño del archivo (máximo 5MB)
            max_size = 5 * 1024 * 1024
            image_file.seek(0, 2)
            file_size = image_file.tell()
            image_file.seek(0)

            if file_size > max_size:
                return request.make_json_response({"code": 400, "msg": "El archivo es demasiado grande. Tamaño máximo: 5MB"})

            # Leer el contenido del archivo y codificarlo a base64
            image_data_bytes = image_file.read()
            image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

            # Guardar la imagen codificada en base64 y la observación
            linea_recepcion.sudo().write({"imagen_observation": image_data_base64})

            # Generar URLs para ver la imagen
            base_url = request.httprequest.host_url.rstrip("/")
            image_url = f"{base_url}/api/view_imagen_observation/batch/{id_linea_recepcion}"
            json_url = f"{base_url}/api/get_imagen_observation/batch/{id_linea_recepcion}"

            return request.make_json_response(
                {"code": 200, "result": "Imagen de observación guardada correctamente en batch", "line_id": id_linea_recepcion, "filename": image_file.filename, "image_size": len(image_data_bytes), "image_url": image_url, "json_url": json_url, "batch_type": "observation"}
            )

        except Exception as e:
            return request.make_json_response({"code": 500, "msg": "Error interno del servidor"})


    # ==========================================
    # ENDPOINTS PARA VISUALIZAR IMÁGENES BATCH
    # ==========================================

    @http.route("/api/view_imagen_linea_recepcion/batch/<int:line_id>", auth="user", type="http", methods=["GET"], csrf=False)
    def view_imagen_linea_recepcion_batch(self, line_id, **kw):
        """
        Endpoint para visualizar la imagen de una línea de recepción batch (campo 'imagen')
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return request.make_response("Línea de recepción batch no encontrada", status=404, headers=[("Content-Type", "text/plain")])

            # Verificar si tiene imagen
            if not linea_recepcion.imagen:
                return request.make_response("No hay imagen disponible para esta línea batch", status=404, headers=[("Content-Type", "text/plain")])

            # Decodificar la imagen de base64
            try:
                image_data = base64.b64decode(linea_recepcion.imagen)
            except Exception as e:
                return request.make_response("Error al procesar la imagen batch", status=500, headers=[("Content-Type", "text/plain")])

            # Detectar el tipo de contenido de la imagen
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

            # Crear la respuesta con la imagen
            response = request.make_response(image_data, headers=[("Content-Type", content_type), ("Content-Length", str(len(image_data))), ("Cache-Control", "public, max-age=3600"), ("Content-Disposition", f"inline; filename=batch_recepcion_{line_id}.jpg")])

            return response

        except Exception as e:
            return request.make_response("Error interno del servidor", status=500, headers=[("Content-Type", "text/plain")])

    @http.route("/api/view_imagen_observation/batch/<int:line_id>", auth="user", type="http", methods=["GET"], csrf=False)
    def view_imagen_observation_batch(self, line_id, **kw):
        """
        Endpoint para visualizar la imagen de observación batch (campo 'imagen_observation')
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return request.make_response("Línea de recepción batch no encontrada", status=404, headers=[("Content-Type", "text/plain")])

            # Verificar si tiene imagen de observación
            if not linea_recepcion.imagen_observation:
                return request.make_response("No hay imagen de observación disponible para esta línea batch", status=404, headers=[("Content-Type", "text/plain")])

            # Decodificar la imagen de base64
            try:
                image_data = base64.b64decode(linea_recepcion.imagen_observation)
            except Exception as e:
                return request.make_response("Error al procesar la imagen de observación batch", status=500, headers=[("Content-Type", "text/plain")])

            # Detectar el tipo de contenido de la imagen
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

            # Crear la respuesta con la imagen
            response = request.make_response(image_data, headers=[("Content-Type", content_type), ("Content-Length", str(len(image_data))), ("Cache-Control", "public, max-age=3600"), ("Content-Disposition", f"inline; filename=batch_observation_{line_id}.jpg")])

            return response

        except Exception as e:
            return request.make_response("Error interno del servidor", status=500, headers=[("Content-Type", "text/plain")])

    @http.route("/api/get_imagen_linea_recepcion/batch/<int:line_id>", auth="user", type="json", methods=["GET"], csrf=False)
    def get_imagen_linea_recepcion_batch_json(self, line_id, **kw):
        """
        Endpoint que devuelve la imagen de línea de recepción batch en formato JSON
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return {"code": 404, "msg": "Línea de recepción batch no encontrada"}

            # Verificar si tiene imagen
            if not linea_recepcion.imagen:
                return {"code": 404, "msg": "No hay imagen disponible para esta línea batch"}

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
                    "temperature": linea_recepcion.temperature if hasattr(linea_recepcion, "temperature") else None,
                    "move_id": linea_recepcion.move_id.id if linea_recepcion.move_id else None,
                    "product_name": linea_recepcion.product_id.name if linea_recepcion.product_id else None,
                    "product_code": linea_recepcion.product_id.default_code if linea_recepcion.product_id else None,
                    "qty_done": linea_recepcion.qty_done,
                    "location_dest": linea_recepcion.location_dest_id.name if linea_recepcion.location_dest_id else None,
                    "batch_type": "image_recepcion",
                },
            }

        except Exception as e:
            return {"code": 500, "msg": "Error interno del servidor"}

    @http.route("/api/get_imagen_observation/batch/<int:line_id>", auth="user", type="json", methods=["GET"], csrf=False)
    def get_imagen_observation_batch_json(self, line_id, **kw):
        """
        Endpoint que devuelve la imagen de observación batch en formato JSON
        """
        try:
            # Buscar la línea de recepción por ID
            linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", line_id)], limit=1)

            if not linea_recepcion:
                return {"code": 404, "msg": "Línea de recepción batch no encontrada"}

            # Verificar si tiene imagen de observación
            if not linea_recepcion.imagen_observation:
                return {"code": 404, "msg": "No hay imagen de observación disponible para esta línea batch"}

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
                    "product_name": linea_recepcion.product_id.name if linea_recepcion.product_id else None,
                    "product_code": linea_recepcion.product_id.default_code if linea_recepcion.product_id else None,
                    "qty_done": linea_recepcion.qty_done,
                    "location_dest": linea_recepcion.location_dest_id.name if linea_recepcion.location_dest_id else None,
                    "batch_type": "observation",
                },
            }

        except Exception as e:
            return {"code": 500, "msg": "Error interno del servidor"}

    ## GET Batchs packing unificado
    @http.route("/api/batch_packing_unificado/2", type="json", auth="user", methods=["GET"], csrf=False)
    def get_batchs_packing_unificado_2(self, **kwargs):
        """
        Endpoint para obtener batches con líneas unificadas de packing
        """
        try:

            version_app = kwargs.get("version_app")

            # 1. Validación de versión (igual que el original)
            response_version = self.get_last_version()
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            update_required = False
            if version_app:
                app_parts = list(map(int, version_app.split(".")))
                latest_parts = list(map(int, latest_version_str.split(".")))
                if app_parts < latest_parts:
                    update_required = True
            else:
                update_required = True

            user = request.env.user
            if not user:
                return {"code": 401, "update_version": update_required, "msg": "Usuario no autenticado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_batch = []
            base_url = request.httprequest.host_url.rstrip("/")

            # Obtener configuración y almacenes
            config_picking = request.env["packing.config.general"].sudo().browse(1)
            allowed_warehouses = obtener_almacenes_usuario(user)

            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # Iterar sobre almacenes permitidos
            for warehouse in allowed_warehouses:
                delivery_steps = warehouse.delivery_steps
                if not delivery_steps:
                    continue

                # Determinar sequence_code (igual que el original)
                if delivery_steps == "ship_only":
                    sequence_code = "OUT"
                elif delivery_steps == "pick_ship":
                    sequence_code = "OUT"
                elif delivery_steps == "pick_pack_ship":
                    sequence_code = "PACK"
                else:
                    continue

                search_domain = [
                    ("state", "=", "in_progress"),
                    ("picking_type_id.sequence_code", "=", sequence_code),
                    ("picking_type_id.warehouse_id", "=", warehouse.id),
                    ("move_line_unified_pack_ids", "!=", False),
                ]

                if config_picking.packing_type == "responsible":
                    search_domain.append(("user_id", "=", user.id))

                # Buscar batches con líneas unificadas
                batches = request.env["stock.picking.batch"].sudo().search(search_domain)

                for batch in batches:
                    if batch.move_line_unified_pack_ids:
                        user_info = {
                            "user_id": batch.user_id.id if batch.user_id else 0,
                            "user_name": batch.user_id.name if batch.user_id else "Desconocido",
                        }

                        # Verificar manejo de temperatura desde líneas unificadas
                        manejo_temperatura = False
                        productos_con_temperatura = batch.move_line_unified_pack_ids.mapped("product_id").filtered(lambda p: hasattr(p, "temperature_control") and p.temperature_control)
                        if productos_con_temperatura:
                            manejo_temperatura = True

                        # Origins (igual que original)
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

                        # Estructura base del batch
                        array_batch_temp = {
                            "id": batch.id,
                            "name": batch.name,
                            "scheduleddate": batch.scheduled_date,
                            "state": batch.state,
                            "user_id": user_info["user_id"] if batch.user_id else 0,
                            "user_name": user_info["user_name"] if batch.user_id else "",
                            "order_by": picking_strategy.picking_priority_app if picking_strategy else "",
                            "order_picking": picking_strategy.picking_order_app if picking_strategy else "",
                            "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                            "cantidad_pedidos": 0,
                            "start_time_pack": batch.start_time_pack or "",
                            "end_time_pack": batch.end_time_pack or "",
                            "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "N/A",
                            "zona_entrega_tms": "",
                            "order_tms": "",
                            "temperatura": batch.temperature_batch if hasattr(batch, "temperature_batch") else "",
                            "manejo_temperatura": manejo_temperatura,
                            "origin": origin_details,
                            "lista_pedidos": [],
                            # Campos específicos para unificado
                            "is_unified": True,
                            "total_unified_lines": len(batch.move_line_unified_pack_ids),
                            "button_get_value_batch_pack": batch.button_get_value_batch_pack,
                            "button_is_set_value_batch_pack_done": batch.button_is_set_value_batch_pack_done,
                        }

                        valid_pickings_found = False

                        # PROCESAR POR PICKING (igual que el original) pero con datos unificados
                        for picking in batch.picking_ids:
                            pedido = {
                                "id": picking.id,
                                "name": picking.name,
                                "batch_id": batch.id,
                                "referencia": picking.origin if picking.origin else "",
                                "contacto": picking.partner_id.id if picking.partner_id else 0,
                                "contacto_name": picking.partner_id.name if picking.partner_id else "N/A",
                                "tipo_operacion": picking.picking_type_id.name if picking.picking_type_id else "N/A",
                                "cantidad_productos": len(picking.move_line_ids.filtered(lambda ml: not ml.is_done_item_pack)),
                                "cantidad_productos_total": len(picking.move_line_ids),
                                "zona_entrega": picking.delivery_zone_id.name if picking.delivery_zone_id else "",
                                # "zona_entrega_tms": picking.delivery_zone_tms if picking.delivery_zone_tms else "",
                                # "order_tms": picking.order_tms if picking.order_tms else "",
                                "zona_entrega_tms": "",
                                "order_tms": "",
                                "numero_paquetes": len(picking.move_line_ids.mapped("package_id")),
                                "lista_productos": [],
                                "lista_paquetes": [],
                            }

                            # Buscar líneas unificadas que correspondan a este picking
                            # (basándose en las líneas originales que generaron la unificación)
                            unified_lines_for_picking = []

                            for unified_line in batch.move_line_unified_pack_ids:
                                # Buscar si hay stock.move.line de este picking que coincida
                                original_lines = request.env["stock.move.line"].search(
                                    [
                                        ("picking_id", "=", picking.id),
                                        ("product_id", "=", unified_line.product_id.id),
                                        ("lot_id", "=", unified_line.lot_id.id),
                                        ("location_id", "=", unified_line.location_id.id),
                                    ]
                                )

                                if original_lines:
                                    unified_lines_for_picking.append(unified_line)

                            # Procesar líneas unificadas para este picking
                            for unified_line in unified_lines_for_picking:
                                product = unified_line.product_id
                                lot = unified_line.lot_id
                                location = unified_line.location_id
                                location_dest = unified_line.location_dest_id

                                # Códigos de barras del producto (igual que original)
                                array_all_barcode = []
                                if "barcode_ids" in product.fields_get():
                                    array_all_barcode = [
                                        {
                                            "barcode": barcode.name,
                                            "batch_id": batch.id,
                                            "id_move": unified_line.id,
                                            "id_product": product.id,
                                            "cantidad": 1,
                                            "product_id": product.id
                                        }
                                        for barcode in product.barcode_ids
                                        if barcode.name  # Filtra solo los barcodes válidos
                                    ]

                                # Empaques del producto (igual que original)
                                array_packing = (
                                    [
                                        {
                                            "id": pack.id,
                                            "name": pack.name,
                                            "qty": pack.qty,
                                            "barcode": pack.barcode,
                                            "id_move": unified_line.id,
                                            "id_product": product.id,
                                        }
                                        for pack in product.packaging_ids
                                        if pack.barcode
                                    ]
                                    if product.packaging_ids
                                    else []
                                )

                                # Solo agregar si NO está marcado como hecho (igual que original)
                                if unified_line.is_done_item == False:
                                    productos = {
                                        "id_move": unified_line.id,
                                        "product_id": [product.id, product.display_name],
                                        "batch_id": batch.id,
                                        "pedido_id": picking.id,
                                        "id_product": product.id if product else 0,
                                        "product_code": product.default_code if product else "",
                                        "picking_id": picking.id,
                                        "lote_id": lot.id if lot else "",
                                        "lot_id": [lot.id, lot.name if lot else ""] if lot else [],
                                        "expire_date": lot.expiration_date or "",
                                        "location_id": [location.id, location.display_name if location else ""],
                                        "barcode_location": location.barcode if location else "",
                                        "location_dest_id": [location_dest.id, location_dest.name if location_dest else ""],
                                        "barcode_location_dest": location_dest.barcode if location_dest else "",
                                        "other_barcode": array_all_barcode,
                                        # "quantity": unified_line.qty_done,  # Usar qty_done (cantidad editada)
                                        "quantity": unified_line.product_uom_qty,  # Usar qty_done (cantidad editada)
                                        "tracking": product.tracking if product else "",
                                        "barcode": product.barcode if product else "",
                                        "product_packing": array_packing,
                                        "weight": product.weight if product else 0,
                                        "unidades": product.uom_id.name if product.uom_id else "UND",
                                        "rimoval_priority": location.priority_picking_desplay if location else 0,
                                        "maneja_temperatura": product.temperature_control if hasattr(product, "temperature_control") else False,
                                        "temperatura": unified_line.temperature if hasattr(unified_line, "temperature") else 0,
                                        # Campos adicionales específicos de unificado
                                        "product_uom_qty_original": unified_line.product_uom_qty,
                                        "qty_done_unified": unified_line.qty_done,
                                        "result_package_id": unified_line.result_package_id.id if unified_line.result_package_id else None,
                                        "package_name": unified_line.result_package_id.name if unified_line.result_package_id else "",
                                        "is_unified_line": True,
                                    }

                                    pedido["lista_productos"].append(productos)

                            # Procesar paquetes ya creados (igual que original pero usando líneas unificadas)
                            unified_lines_with_packages = batch.move_line_unified_pack_ids.filtered(lambda ul: ul.result_package_id and ul.is_done_item)

                            unique_packages = unified_lines_with_packages.mapped("result_package_id")

                            for pack in unique_packages:
                                # Filtrar líneas unificadas que pertenecen a este paquete y picking
                                unified_lines_in_package = unified_lines_with_packages.filtered(lambda ul: ul.result_package_id == pack)

                                # Verificar que al menos una línea corresponda a este picking
                                belongs_to_picking = False
                                for ul in unified_lines_in_package:
                                    original_lines = request.env["stock.move.line"].search(
                                        [
                                            ("picking_id", "=", picking.id),
                                            ("product_id", "=", ul.product_id.id),
                                            ("lot_id", "=", ul.lot_id.id),
                                            ("location_id", "=", ul.location_id.id),
                                        ]
                                    )
                                    if original_lines:
                                        belongs_to_picking = True
                                        break

                                if not belongs_to_picking:
                                    continue

                                cantidad_productos = len(unified_lines_in_package)

                                package = {
                                    "name": pack.name,
                                    "id": pack.id,
                                    "batch_id": batch.id,
                                    "pedido_id": picking.id,
                                    "cantidad_productos": cantidad_productos,
                                    "lista_productos_in_packing": [],
                                    "is_sticker": pack.is_sticker if hasattr(pack, "is_sticker") else False,
                                    "is_certificate": pack.is_certificate if hasattr(pack, "is_certificate") else False,
                                    "fecha_creacion": pack.create_date.strftime("%Y-%m-%d") if pack.create_date else "",
                                    "fecha_actualizacion": pack.write_date.strftime("%Y-%m-%d") if pack.write_date else "",
                                    "consecutivo": getattr(unified_lines_in_package[0], "faber_box_number", "") if unified_lines_in_package else "",
                                }

                                for unified_line in unified_lines_in_package:
                                    product = unified_line.product_id
                                    lot = unified_line.lot_id

                                    product_in_packing = {
                                        "id_move": unified_line.id,
                                        "pedido_id": picking.id,
                                        "batch_id": batch.id,
                                        "package_name": pack.name,
                                        "quantity_separate": unified_line.qty_done,
                                        "id_product": product.id if product else 0,
                                        "product_id": [product.id, product.display_name],
                                        "name_packing": pack.name,
                                        "cantidad_enviada": unified_line.qty_done,
                                        "unidades": product.uom_id.name if product.uom_id else "UND",
                                        "peso": product.weight if product else 0,
                                        "lote_id": [lot.id, lot.name if lot else ""] if lot else [],
                                        "observation": unified_line.new_observation,
                                        "weight": product.weight if product else 0,
                                        "is_sticker": pack.is_sticker if hasattr(pack, "is_sticker") else False,
                                        "is_certificate": pack.is_certificate if hasattr(pack, "is_certificate") else False,
                                        "id_package": pack.id,
                                        "quantity": unified_line.qty_done,
                                        "tracking": product.tracking if product else "",
                                        "maneja_temperatura": product.temperature_control if hasattr(product, "temperature_control") else False,
                                        "temperatura": unified_line.temperature if hasattr(unified_line, "temperature") else 0,
                                        "time_separate": unified_line.time if unified_line.time else 0,
                                        # Campos específicos de línea unificada
                                        "is_unified_line": True,
                                        "product_uom_qty_original": unified_line.product_uom_qty,
                                    }

                                    package["lista_productos_in_packing"].append(product_in_packing)

                                pedido["lista_paquetes"].append(package)

                            # Solo agregar el pedido si tiene productos
                            if pedido["lista_productos"]:
                                array_batch_temp["lista_pedidos"].append(pedido)
                                valid_pickings_found = True

                        # Agregar batch al array final si es válido
                        if valid_pickings_found:
                            array_batch_temp["cantidad_pedidos"] = len(array_batch_temp["lista_pedidos"])
                            array_batch.append(array_batch_temp)

            return {"code": 200, "update_version": update_required, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "update_version": update_required, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "update_version": update_required, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/batch_packing_unificado", type="json", auth="user", methods=["GET"], csrf=False)
    def get_batchs_packing_unificado(self, **kwargs):
        """
        Endpoint para obtener batches con líneas unificadas de packing
        Devuelve UN SOLO PEDIDO UNIFICADO con todos los productos
        """
        try:
            version_app = kwargs.get("version_app")

            # 1. Validación de versión (igual que el original)
            response_version = self.get_last_version()
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            update_required = False
            if version_app:
                app_parts = list(map(int, version_app.split(".")))
                latest_parts = list(map(int, latest_version_str.split(".")))
                if app_parts < latest_parts:
                    update_required = True
            else:
                update_required = True

            user = request.env.user
            if not user:
                return {"code": 401, "update_version": update_required, "msg": "Usuario no autenticado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_batch = []
            base_url = request.httprequest.host_url.rstrip("/")

            # Obtener configuración y almacenes
            config_picking = request.env["packing.config.general"].sudo().browse(1)
            allowed_warehouses = obtener_almacenes_usuario(user)

            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # Iterar sobre almacenes permitidos
            for warehouse in allowed_warehouses:
                delivery_steps = warehouse.delivery_steps
                if not delivery_steps:
                    continue

                # Determinar sequence_code (igual que el original)
                if delivery_steps == "ship_only":
                    sequence_code = "OUT"
                elif delivery_steps == "pick_ship":
                    sequence_code = "OUT"
                elif delivery_steps == "pick_pack_ship":
                    sequence_code = "PACK"
                else:
                    continue

                search_domain = [
                    ("state", "=", "in_progress"),
                    ("picking_type_id.sequence_code", "=", sequence_code),
                    ("picking_type_id.warehouse_id", "=", warehouse.id),
                    ("move_line_unified_pack_ids", "!=", False),
                ]

                if config_picking.packing_type == "responsible":
                    search_domain.append(("user_id", "in", [user.id, False]))

                # Buscar batches con líneas unificadas
                batches = request.env["stock.picking.batch"].sudo().search(search_domain)

                for batch in batches:
                    if batch.move_line_unified_pack_ids:

                        # Verificar si hay líneas pendientes
                        lineas_pendientes = batch.move_line_unified_pack_ids.filtered(lambda ul: ul.is_done_item == False)

                        if not lineas_pendientes:
                            continue

                        user_info = {
                            "user_id": batch.user_id.id if batch.user_id else 0,
                            "user_name": batch.user_id.name if batch.user_id else "Desconocido",
                        }

                        # Verificar manejo de temperatura desde líneas unificadas
                        manejo_temperatura = False
                        productos_con_temperatura = batch.move_line_unified_pack_ids.mapped("product_id").filtered(lambda p: hasattr(p, "temperature_control") and p.temperature_control)
                        if productos_con_temperatura:
                            manejo_temperatura = True

                        # Origins (igual que original)
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

                        # Estructura base del batch
                        array_batch_temp = {
                            "id": batch.id,
                            "name": batch.name,
                            "scheduleddate": batch.scheduled_date,
                            "state": batch.state,
                            "user_id": user_info["user_id"] if batch.user_id else 0,
                            "user_name": user_info["user_name"] if batch.user_id else "",
                            "order_by": picking_strategy.picking_priority_app if picking_strategy else "",
                            "order_picking": picking_strategy.picking_order_app if picking_strategy else "",
                            "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                            "cantidad_pedidos": 1,  # SIEMPRE 1 porque es unificado
                            "start_time_pack": batch.start_time_pack or "",
                            "end_time_pack": batch.end_time_pack or "",
                            "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "N/A",
                            "zona_entrega_tms": "",
                            "order_tms": "",
                            "temperatura": batch.temperature_batch if hasattr(batch, "temperature_batch") else "",
                            "manejo_temperatura": manejo_temperatura,
                            "origin": origin_details,
                            "origins": ", ".join([picking.origin for picking in batch.picking_ids if picking.origin]) or "Sin origins",
                            "cantidad_total_pedidos": len(batch.picking_ids),
                            "cantidad_total_productos": len(batch.move_line_unified_pack_ids),
                            "unidades_productos": sum(batch.move_line_unified_pack_ids.mapped("product_uom_qty")),
                            "lista_pedidos": [],
                            # Campos específicos para unificado
                            "is_unified": True,
                            "total_unified_lines": len(batch.move_line_unified_pack_ids),
                            "button_get_value_batch_pack": batch.button_get_value_batch_pack,
                            "button_is_set_value_batch_pack_done": batch.button_is_set_value_batch_pack_done,
                        }

                        # ✅ CREAR UN SOLO PEDIDO UNIFICADO
                        pedido_unificado = {
                            "id": batch.id,  # Usar ID del batch como ID del pedido unificado
                            "name": batch.name,
                            "referencia": picking.origin if picking.origin else "",
                            "batch_id": batch.id,
                            "contacto": picking.partner_id.id if picking.partner_id else 0,
                            "contacto_name": picking.partner_id.name if picking.partner_id else "N/A",
                            "tipo_operacion": picking.picking_type_id.name if picking.picking_type_id else "N/A",
                            "cantidad_productos": len(picking.move_line_ids.filtered(lambda ml: not ml.is_done_item_pack)),
                            "zona_entrega": picking.delivery_zone_id.name if picking.delivery_zone_id else "",
                            # "zona_entrega_tms": picking.delivery_zone_tms if picking.delivery_zone_tms else "",
                            # "order_tms": picking.order_tms if picking.order_tms else "",
                            "zona_entrega_tms": "",
                            "order_tms": "",
                            "pedidos": ", ".join([picking.name for picking in batch.picking_ids if picking.name]) or "Sin pickings",
                            "lista_productos": [],
                            "lista_paquetes": [],
                            "is_unified": True,
                            "numero_paquetes": 0,
                        }

                        # ✅ PROCESAR TODAS LAS LÍNEAS UNIFICADAS SIN FILTRAR POR PICKING
                        for unified_line in batch.move_line_unified_pack_ids:
                            product = unified_line.product_id
                            lot = unified_line.lot_id
                            location = unified_line.location_id
                            location_dest = unified_line.location_dest_id

                            # Códigos de barras del producto
                            array_all_barcode = []
                            if "barcode_ids" in product.fields_get():
                                array_all_barcode = [
                                    {
                                        "barcode": barcode.name,
                                        "batch_id": batch.id,
                                        "id_move": unified_line.id,
                                        "id_product": product.id,
                                        "cantidad": 1,
                                        "product_id": product.id
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name  # Filtra solo los barcodes válidos
                                ]

                            # Empaques del producto
                            array_packing = (
                                [
                                    {
                                        "id": pack.id,
                                        "name": pack.name,
                                        "qty": pack.qty,
                                        "barcode": pack.barcode,
                                        "id_move": unified_line.id,
                                        "id_product": product.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]
                                if product.packaging_ids
                                else []
                            )

                            # Solo agregar si NO está marcado como hecho
                            if unified_line.is_done_item == False:
                                productos = {
                                    "id_move": unified_line.id,
                                    "product_id": [product.id, product.display_name],
                                    "batch_id": batch.id,
                                    "pedido_id": batch.id,  # Usar ID del batch
                                    "id_product": product.id if product else 0,
                                    "product_code": product.default_code if product else "",
                                    "picking_id": batch.id,  # Usar ID del batch
                                    "lote_id": lot.id if lot else "",
                                    "lot_id": [lot.id, lot.name if lot else ""] if lot else [],
                                    "expire_date": lot.expiration_date or "",
                                    "location_id": [location.id, location.display_name if location else ""],
                                    "barcode_location": location.barcode if location else "",
                                    "location_dest_id": [location_dest.id, location_dest.name if location_dest else ""],
                                    "barcode_location_dest": location_dest.barcode if location_dest else "",
                                    "other_barcode": array_all_barcode,
                                    "quantity": unified_line.product_uom_qty,
                                    "tracking": product.tracking if product else "",
                                    "barcode": product.barcode if product else "",
                                    "product_packing": array_packing,
                                    "weight": product.weight if product else 0,
                                    "unidades": product.uom_id.name if product.uom_id else "UND",
                                    "rimoval_priority": location.priority_picking_desplay if location else 0,
                                    "maneja_temperatura": product.temperature_control if hasattr(product, "temperature_control") else False,
                                    "temperatura": unified_line.temperature if hasattr(unified_line, "temperature") else 0,
                                    # Campos adicionales específicos de unificado
                                    "product_uom_qty_original": unified_line.product_uom_qty,
                                    "qty_done_unified": unified_line.qty_done,
                                    "result_package_id": unified_line.result_package_id.id if unified_line.result_package_id else None,
                                    "package_name": unified_line.result_package_id.name if unified_line.result_package_id else "",
                                    "is_unified_line": True,
                                }

                                pedido_unificado["lista_productos"].append(productos)

                        # ✅ PROCESAR PAQUETES UNIFICADOS (SIN FILTRAR POR PICKING)
                        unified_lines_with_packages = batch.move_line_unified_pack_ids.filtered(lambda ul: ul.result_package_id and ul.is_done_item)

                        unique_packages = unified_lines_with_packages.mapped("result_package_id")

                        for pack in unique_packages:
                            # Todas las líneas unificadas que pertenecen a este paquete
                            unified_lines_in_package = unified_lines_with_packages.filtered(lambda ul: ul.result_package_id == pack)

                            cantidad_productos = len(unified_lines_in_package)

                            package = {
                                "name": pack.name,
                                "id": pack.id,
                                "batch_id": batch.id,
                                "pedido_id": batch.id,  # Usar ID del batch
                                "cantidad_productos": cantidad_productos,
                                "lista_productos_in_packing": [],
                                "is_sticker": pack.is_sticker if hasattr(pack, "is_sticker") else False,
                                "is_certificate": pack.is_certificate if hasattr(pack, "is_certificate") else False,
                                "fecha_creacion": pack.create_date.strftime("%Y-%m-%d") if pack.create_date else "",
                                "fecha_actualizacion": pack.write_date.strftime("%Y-%m-%d") if pack.write_date else "",
                                "consecutivo": getattr(unified_lines_in_package[0], "faber_box_number", "") if unified_lines_in_package else "",
                            }

                            for unified_line in unified_lines_in_package:
                                product = unified_line.product_id
                                lot = unified_line.lot_id

                                product_in_packing = {
                                    "id_move": unified_line.id,
                                    "pedido_id": batch.id,  # Usar ID del batch
                                    "batch_id": batch.id,
                                    "package_name": pack.name,
                                    "quantity_separate": unified_line.qty_done,
                                    "id_product": product.id if product else 0,
                                    "product_id": [product.id, product.display_name],
                                    "name_packing": pack.name,
                                    "cantidad_enviada": unified_line.qty_done,
                                    "unidades": product.uom_id.name if product.uom_id else "UND",
                                    "peso": product.weight if product else 0,
                                    "lote_id": [lot.id, lot.name if lot else ""] if lot else [],
                                    "observation": unified_line.new_observation,
                                    "weight": product.weight if product else 0,
                                    "is_sticker": pack.is_sticker if hasattr(pack, "is_sticker") else False,
                                    "is_certificate": pack.is_certificate if hasattr(pack, "is_certificate") else False,
                                    "id_package": pack.id,
                                    "quantity": unified_line.qty_done,
                                    "tracking": product.tracking if product else "",
                                    "maneja_temperatura": product.temperature_control if hasattr(product, "temperature_control") else False,
                                    "temperatura": unified_line.temperature if hasattr(unified_line, "temperature") else 0,
                                    "time_separate": unified_line.time if unified_line.time else 0,
                                    # Campos específicos de línea unificada
                                    "is_unified_line": True,
                                    "product_uom_qty_original": unified_line.product_uom_qty,
                                }

                                package["lista_productos_in_packing"].append(product_in_packing)

                            pedido_unificado["lista_paquetes"].append(package)

                        # ✅ AGREGAR EL ÚNICO PEDIDO UNIFICADO
                        if pedido_unificado["lista_productos"] or pedido_unificado["lista_paquetes"]:
                            array_batch_temp["lista_pedidos"].append(pedido_unificado)
                            array_batch.append(array_batch_temp)

            return {"code": 200, "update_version": update_required, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "update_version": update_required, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "update_version": update_required, "msg": f"Error inesperado: {str(err)}"}

    """
    Endpoint para empaquetar producto unificados
    """

    @http.route("/api/send_pack_unified", type="json", auth="user", methods=["POST"], csrf=False)
    def send_pack_unified(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            id_batch = auth.get("id_batch")
            list_item = auth.get("list_item", [])
            is_sticker = auth.get("is_sticker", False)
            is_certificate = auth.get("is_certificate", False)
            peso_total_paquete = auth.get("peso_total_paquete", 0)

            array_msg = []

            # Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            # Crear el paquete manualmente
            pack = (
                request.env["stock.quant.package"]
                .sudo()
                .create(
                    {
                        "is_sticker": is_sticker,
                        "is_certificate": is_certificate,
                    }
                )
            )

            for move in list_item:
                cantidad_separada = move.get("cantidad_separada", 0)
                id_move = move.get("id_move")  # ID de move.line.unified.pack
                observacion = move.get("observacion", "")
                id_operario = move.get("id_operario", 0)
                fecha_transaccion = move.get("fecha_transaccion", "")
                time = move.get("time_line", 0)
                dividir = move.get("dividir", False)  # NUEVO PARÁMETRO

                # Buscar la línea unificada
                unified_line = request.env["move.line.unified.pack"].sudo().browse(id_move)

                if not unified_line.exists():
                    array_msg.append(
                        {
                            "code": 400,
                            "msg": f"Error: la línea unificada {id_move} no existe",
                        }
                    )
                    continue

                # Validar cantidad
                if cantidad_separada > unified_line.product_uom_qty:
                    array_msg.append(
                        {
                            "code": 400,
                            "msg": f"La cantidad separada {cantidad_separada} excede la cantidad demandada inicial {unified_line.product_uom_qty}",
                        }
                    )
                    continue

                if dividir:
                    cantidad_original = unified_line.product_uom_qty
                    cantidad_restante = cantidad_original - cantidad_separada

                    if cantidad_restante > 0:
                        # Actualizar línea original con cantidad restante
                        unified_line.write({
                            "product_uom_qty": cantidad_restante,
                        })
                        
                        # Crear nueva línea empaquetada
                        new_line_vals = {
                            "product_id": unified_line.product_id.id,
                            "product_uom_id": unified_line.product_uom_id.id,
                            "lot_id": unified_line.lot_id.id if unified_line.lot_id else False,
                            "location_id": unified_line.location_id.id,
                            "location_dest_id": unified_line.location_dest_id.id,
                            "product_uom_qty": cantidad_separada,
                            "qty_done": cantidad_separada,
                            "stock_picking_batch_id": unified_line.stock_picking_batch_id.id,
                            "result_package_id": pack.id,
                            "new_observation": observacion if observacion.lower() != "sin novedad" else "",
                            "user_operator_id": id_operario,
                            "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                            "is_done_item": True,
                            "time": time,
                        }
                        request.env["move.line.unified.pack"].sudo().create(new_line_vals)
                        
                    else:
                        unified_line.write({
                            "product_uom_qty": cantidad_separada,
                            "qty_done": cantidad_separada,
                            "result_package_id": pack.id,
                            "new_observation": observacion if observacion.lower() != "sin novedad" else "",
                            "user_operator_id": id_operario,
                            "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                            "is_done_item": True,
                            "time": time,
                        })

                else:
                    # NO DIVIDIR - Solo actualizar la línea existente
                    unified_line.write(
                        {
                            "qty_done": cantidad_separada,
                            "result_package_id": pack.id,
                            "new_observation": observacion if observacion.lower() != "sin novedad" else "",
                            "user_operator_id": id_operario,
                            "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                            "is_done_item": True,
                            "time": time,
                        }
                    )

            # Generar números de caja
            batch._regenerate_box_numbers_pack()

            # Obtener el consecutivo del paquete
            consecutivo = "Caja1"  # valor por defecto
            unified_lines_with_package = batch.move_line_unified_pack_ids.filtered(lambda ul: ul.result_package_id and ul.result_package_id.id == pack.id)

            if unified_lines_with_package:
                primera_linea = unified_lines_with_package[0]
                consecutivo = primera_linea.faber_box_number or "Caja1"

            array_msg.append(
                {
                    "id_paquete": pack.id,
                    "name_paquete": pack.name,
                    "id_batch": batch.id,
                    "cantidad_productos_en_el_paquete": len(list_item),
                    "is_sticker": is_sticker,
                    "is_certificate": is_certificate,
                    "peso": peso_total_paquete,
                    "consecutivo": consecutivo,
                    "list_item": list_item,
                    "tipo_empaquetado": "unificado",
                }
            )

            return {"code": 200, "result": array_msg}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except ValidationError as e:
            return {"code": 400, "msg": f"Error de validación: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    """
    Endpoints para desempaquetar producto unificados
    """

    @http.route("/api/unpack_unified", type="json", auth="user", methods=["POST"], csrf=False)
    def unpack_unified(self, **auth):
        try:
            # Validar autenticación
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            id_batch = auth.get("id_batch")
            id_paquete = auth.get("id_paquete")
            list_item = auth.get("list_item", [])

            array_msg = []

            # Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            # Validar si el paquete existe
            paquete = request.env["stock.quant.package"].sudo().browse(id_paquete)
            if not paquete.exists():
                return {"code": 400, "msg": f"El paquete {id_paquete} no existe"}

            productos_procesados = 0

            # Procesar cada ítem en la lista
            for move in list_item:
                product_id = move.get("product_id")
                location_id = move.get("location_id")
                lote = move.get("lote", None)
                cantidad_separada = move.get("cantidad_separada", 0)
                id_move = move.get("id_move")  # ID de move.line.unified.pack
                observacion = move.get("observacion", "")
                id_operario = move.get("id_operario", 0)
                fecha_transaccion = move.get("fecha_transaccion", "")

                # Buscar en move.line.unified.pack
                unified_line = request.env["move.line.unified.pack"].sudo().browse(id_move)

                if not unified_line.exists():
                    array_msg.append(
                        {
                            "code": 400,
                            "msg": f"Error: la línea unificada {id_move} no existe",
                        }
                    )
                    continue

                # VALIDACIÓN MEJORADA: Verificar si la línea tiene ese paquete específico
                if unified_line.result_package_id:
                    if unified_line.result_package_id.id == id_paquete:
                        # CASO 1: La línea SÍ tiene el paquete correcto - desempaquetar normalmente
                        unified_line.write(
                            {
                                "result_package_id": False,
                                "qty_done": 0,
                                "new_observation": observacion if observacion.lower() != "sin novedad" else "",
                                "user_operator_id": id_operario,
                                "date_transaction_packing": "",
                                "is_done_item": False,
                                "faber_box_number": False,
                            }
                        )

                        array_msg.append(
                            {
                                "id_move": id_move,
                                "status": "desempaquetado_correcto",
                                "msg": f"Línea {id_move} desempaquetada correctamente del paquete {paquete.name}",
                            }
                        )
                        productos_procesados += 1

                    else:
                        # CASO 2: La línea tiene un paquete DIFERENTE al solicitado
                        paquete_actual = unified_line.result_package_id.name
                        array_msg.append(
                            {
                                "id_move": id_move,
                                "status": "paquete_diferente",
                                "code": 400,
                                "msg": f"La línea {id_move} pertenece al paquete '{paquete_actual}', no al paquete '{paquete.name}' solicitado",
                            }
                        )
                        continue
                else:
                    # linea sin paquete asignado
                    array_msg.append(
                        {
                            "id_move": id_move,
                            "status": "sin_paquete",
                            "msg": f"La línea {id_move} no tiene paquete asignado",
                        }
                    )

            # Verificar si el paquete quedó vacío y eliminarlo
            lineas_en_paquete = batch.move_line_unified_pack_ids.filtered(lambda ul: ul.result_package_id and ul.result_package_id.id == id_paquete)

            paquete_eliminado = False
            if not lineas_en_paquete:
                paquete_name = paquete.name  # Guardar nombre antes de eliminar
                paquete.unlink()
                paquete_eliminado = True
                array_msg.append({"status": "paquete_eliminado", "code": 200, "msg": f"El paquete {paquete_name} ha sido eliminado (quedó vacío)"})

            # Regenerar números de caja (comentado como en tu versión)
            # try:
            #     batch.action_generate_box_numbers()
            # except AttributeError:
            #     pass

            # Respuesta final
            array_msg.append(
                {
                    "id_paquete": None if paquete_eliminado else paquete.id,
                    "name_paquete": "Eliminado" if paquete_eliminado else paquete.name,
                    "id_batch": batch.id,
                    "cantidad_productos_procesados": productos_procesados,
                    "paquete_eliminado": paquete_eliminado,
                    "list_item": list_item,
                    "tipo_desempaquetado": "unificado",
                }
            )

            return {"code": 200, "result": array_msg}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except ValidationError as e:
            return {"code": 400, "msg": f"Error de validación: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ##POST Para enviar la temperatura y la imagen en la linea de movimiento
    # @http.route("/api/send_image_linea_recepcion/batch", auth="user", type="http", methods=["POST"], csrf=False)
    # def send_image_linea_recepcion_batch(self, **post):
    #     try:
    #         user = request.env.user
    #         if not user:
    #             return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

    #         id_linea_recepcion = post.get("move_line_id")
    #         image_file = request.httprequest.files.get("image_data")
    #         temperatura = post.get("temperatura", 0.0)

    #         # Validar ID de línea de recepción
    #         if not id_linea_recepcion:
    #             return request.make_json_response({"code": 400, "msg": "ID de línea de recepción no válido"})

    #         # Validar archivo de imagen
    #         if not image_file:
    #             return request.make_json_response({"code": 400, "msg": "No se recibió ningún archivo de imagen"})

    #         # Convertir ID a entero si viene como string
    #         try:
    #             id_linea_recepcion = int(id_linea_recepcion)
    #         except (ValueError, TypeError):
    #             return request.make_json_response({"code": 400, "msg": "ID de línea de recepción debe ser un número"})

    #         # Buscar la línea de recepción por ID
    #         linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", id_linea_recepcion)], limit=1)

    #         if not linea_recepcion:
    #             return request.make_json_response({"code": 404, "msg": "Línea de recepción no encontrada"})

    #         # Validar tipo de archivo (opcional)
    #         allowed_extensions = ["jpg", "jpeg", "png", "gif", "bmp"]
    #         file_extension = image_file.filename.lower().split(".")[-1] if image_file.filename else ""
    #         if file_extension not in allowed_extensions:
    #             return request.make_json_response({"code": 400, "msg": "Formato de imagen no permitido"})

    #         # Leer el contenido del archivo y codificarlo a base64
    #         image_data_bytes = image_file.read()
    #         image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

    #         # Guardar la imagen codificada en base64
    #         linea_recepcion.sudo().write({"imagen": image_data_base64, "temperature": temperatura})

    #         return request.make_json_response({"code": 200, "result": "Imagen y temperatura guardadas correctamente en la linea del batch", "line_id": id_linea_recepcion})

    #     except Exception as e:
    #         return request.make_json_response({"code": 500, "msg": f"Error interno: {str(e)}"})

    # @http.route("/api/send_imagen_observation/batch", auth="user", type="http", methods=["POST"], csrf=False)
    # def send_imagen_observation_batch(self, **post):
    #     try:
    #         user = request.env.user
    #         if not user:
    #             return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

    #         id_linea_recepcion = post.get("move_line_id")
    #         image_file = request.httprequest.files.get("image_data")

    #         # Validar ID de línea de recepción
    #         if not id_linea_recepcion:
    #             return request.make_json_response({"code": 400, "msg": "ID de línea de recepción no válido"})

    #         # Validar archivo de imagen
    #         if not image_file:
    #             return request.make_json_response({"code": 400, "msg": "No se recibió ningún archivo de imagen"})

    #         # Convertir ID a entero si viene como string
    #         try:
    #             id_linea_recepcion = int(id_linea_recepcion)
    #         except (ValueError, TypeError):
    #             return request.make_json_response({"code": 400, "msg": "ID de línea de recepción debe ser un número"})

    #         # Buscar la línea de recepción por ID
    #         linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", id_linea_recepcion)], limit=1)

    #         if not linea_recepcion:
    #             return request.make_json_response({"code": 404, "msg": "Línea de recepción no encontrada"})

    #         # Validar tipo de archivo (opcional)
    #         allowed_extensions = ["jpg", "jpeg", "png", "gif", "bmp"]
    #         file_extension = image_file.filename.lower().split(".")[-1] if image_file.filename else ""
    #         if file_extension not in allowed_extensions:
    #             return request.make_json_response({"code": 400, "msg": "Formato de imagen no permitido"})

    #         # Leer el contenido del archivo y codificarlo a base64
    #         image_data_bytes = image_file.read()
    #         image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

    #         # Guardar la imagen codificada en base64 y la observación
    #         linea_recepcion.sudo().write({"imagen_observation": image_data_base64})

    #         # 🔥 Generar la URL para ver la imagen
    #         base_url = request.httprequest.host_url.rstrip("/")
    #         image_url = f"{base_url}/api/view_imagen_observation/{id_linea_recepcion}"

    #         # return request.make_json_response({"code": 200, "result": "Imagen de observación guardada correctamente", "recepcion_id": id_linea_recepcion, "image_url": image_url})  # 🔥 URL para ver la imagen
    #         return request.make_json_response({"code": 200, "result": "Imagen de observación guardada correctamente", "recepcion_id": id_linea_recepcion})

    #     except Exception as e:
    #         return request.make_json_response({"code": 500, "msg": f"Error interno: {str(e)}"})

    # ==========================================
    # ENDPOINTS BATCH MEJORADOS
    # ==========================================

    # @http.route("/api/send_image_linea_recepcion/batch", auth="user", type="http", methods=["POST"], csrf=False)
    # def send_image_linea_recepcion_batch(self, **post):
    #     try:
    #         user = request.env.user
    #         if not user:
    #             return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

    #         show_photo_temperature = request.env["appwms.temperature"].sudo().search([], limit=1)

    #         id_linea_recepcion = post.get("move_line_id")
    #         image_file = request.httprequest.files.get("image_data")
    #         temperatura = post.get("temperatura", 0.0)

    #         # Validar ID de línea de recepción
    #         if not id_linea_recepcion:
    #             return request.make_json_response({"code": 400, "msg": "ID de línea de recepción no válido"})

    #         # Validar archivo de imagen
    #         if not image_file and show_photo_temperature.show_photo_temperature:
    #             return request.make_json_response({"code": 400, "msg": "No se recibió ningún archivo de imagen"})

    #         # Convertir ID a entero si viene como string
    #         try:
    #             id_linea_recepcion = int(id_linea_recepcion)
    #         except (ValueError, TypeError):
    #             return request.make_json_response({"code": 400, "msg": "ID de línea de recepción debe ser un número"})

    #         # Buscar la línea de recepción por ID
    #         linea_recepcion = request.env["stock.move.line"].sudo().search([("id", "=", id_linea_recepcion)], limit=1)

    #         if not linea_recepcion:
    #             return request.make_json_response({"code": 404, "msg": "Línea de recepción no encontrada"})

    #         # Validar tipo de archivo
    #         allowed_extensions = ["jpg", "jpeg", "png", "gif", "bmp", "webp"]
    #         file_extension = image_file.filename.lower().split(".")[-1] if image_file.filename else ""
    #         if file_extension not in allowed_extensions:
    #             return request.make_json_response({"code": 400, "msg": f"Formato de imagen no permitido. Formatos válidos: {', '.join(allowed_extensions)}"})

    #         # Validar tamaño del archivo (máximo 5MB)
    #         max_size = 5 * 1024 * 1024
    #         image_file.seek(0, 2)
    #         file_size = image_file.tell()
    #         image_file.seek(0)

    #         if file_size > max_size:
    #             return request.make_json_response({"code": 400, "msg": "El archivo es demasiado grande. Tamaño máximo: 5MB"})

    #         # Validar temperatura
    #         try:
    #             temperatura = float(temperatura)
    #         except (ValueError, TypeError):
    #             return request.make_json_response({"code": 400, "msg": "Temperatura debe ser un número"})

    #         # Leer el contenido del archivo y codificarlo a base64
    #         image_data_bytes = image_file.read()
    #         image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

    #         # Guardar la imagen codificada en base64
    #         linea_recepcion.sudo().write({"imagen": image_data_base64, "temperature": temperatura})

    #         # Generar URLs para ver la imagen
    #         base_url = request.httprequest.host_url.rstrip("/")
    #         image_url = f"{base_url}/api/view_imagen_linea_recepcion/batch/{id_linea_recepcion}"
    #         json_url = f"{base_url}/api/get_imagen_linea_recepcion/batch/{id_linea_recepcion}"

    #         return request.make_json_response(
    #             {
    #                 "code": 200,
    #                 "result": "Imagen y temperatura guardadas correctamente en la línea del batch",
    #                 "line_id": id_linea_recepcion,
    #                 "temperature": temperatura,
    #                 "show_photo_temperature": show_photo_temperature.show_photo_temperature if show_photo_temperature else False,
    #                 "filename": image_file.filename,
    #                 "image_size": len(image_data_bytes),
    #                 "image_url": image_url,
    #                 "json_url": json_url,
    #                 "batch_type": "image_recepcion",
    #             }
    #         )

    #     except Exception as e:
    #         return request.make_json_response({"code": 500, "msg": "Error interno del servidor"})


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


def validate_pda(device_id):
    """
    Solo valida que la PDA existe y está autorizada
    Returns: dict con error si hay problema, None si todo está OK
    """
    if not device_id:
        return {"code": 400, "msg": "Device ID no proporcionado, por favor actualizar a la ultima version de la app"}

    pda = request.env["pda.logs"].sudo().search([("device_id", "=", device_id)])

    if not pda:
        return {"code": 404, "msg": "PDA no encontrado"}

    if pda.is_authorized == "no":
        return {"code": 403, "msg": "PDA no autorizado"}

    # Si llegamos aquí, todo está bien
    return None
