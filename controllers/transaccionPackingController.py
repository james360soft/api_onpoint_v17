# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from datetime import datetime, timedelta
import pytz
import base64


class TransaccionDataPacking(http.Controller):

    ## GET Transacciones batchs para packing
    @http.route("/api/batch_packing", auth="user", type="json", methods=["GET"])
    def get_batch_packing(self):
        try:
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

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
                                        "time_separate": int(move_line.time_packing) if move_line.time_packing else 0,
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

            for move in list_item:
                product_id = move.get("product_id")
                location_id = move.get("location_id")
                lote = move.get("lote", None)
                cantidad_separada = move.get("cantidad_separada", 0)
                id_move = move.get("id_move")
                observacion = move.get("observacion", "")
                id_operario = move.get("id_operario", 0)
                fecha_transaccion = move.get("fecha_transaccion", "")

                # poner la observacion en minuscula

                move_line = request.env["stock.move.line"].sudo().browse(id_move)

                if move_line.exists():
                    if move_line.quantity >= cantidad_separada:

                        if observacion.lower() != "sin novedad":
                            move_line.write(
                                {
                                    "result_package_id": pack.id,
                                    "quantity": cantidad_separada,
                                    "new_observation_packing": observacion,
                                    "user_operator_id": id_operario,
                                    "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                                    "is_done_item_pack": True,
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
                                }
                            )

                            # ✅ 4. Crear la línea nueva con los valores actualizados
                            new_line = request.env["stock.move.line"].sudo().create(new_line_vals)

                            new_line.write({"is_done_item_pack": True})

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

            array_msg.append(
                {
                    "id_paquete": pack.id,
                    "name_paquete": pack.name,
                    "id_batch": batch.id,
                    "cantidad_productos_en_el_paquete": len(list_item),
                    "is_sticker": is_sticker,
                    "is_certificate": is_certificate,
                    "peso": peso_total_paquete,
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

    @http.route("/api/send_image_linea_recepcion/batch", auth="user", type="http", methods=["POST"], csrf=False)
    def send_image_linea_recepcion_batch(self, **post):
        try:
            user = request.env.user
            if not user:
                return request.make_json_response({"code": 400, "msg": "Usuario no encontrado"})

            id_linea_recepcion = post.get("move_line_id")
            image_file = request.httprequest.files.get("image_data")
            temperatura = post.get("temperatura", 0.0)

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

            # Validar temperatura
            try:
                temperatura = float(temperatura)
            except (ValueError, TypeError):
                return request.make_json_response({"code": 400, "msg": "Temperatura debe ser un número"})

            # Leer el contenido del archivo y codificarlo a base64
            image_data_bytes = image_file.read()
            image_data_base64 = base64.b64encode(image_data_bytes).decode("utf-8")

            # Guardar la imagen codificada en base64
            linea_recepcion.sudo().write({"imagen": image_data_base64, "temperature": temperatura})

            # Generar URLs para ver la imagen
            base_url = request.httprequest.host_url.rstrip("/")
            image_url = f"{base_url}/api/view_imagen_linea_recepcion/batch/{id_linea_recepcion}"
            json_url = f"{base_url}/api/get_imagen_linea_recepcion/batch/{id_linea_recepcion}"

            return request.make_json_response(
                {
                    "code": 200,
                    "result": "Imagen y temperatura guardadas correctamente en la línea del batch",
                    "line_id": id_linea_recepcion,
                    "temperature": temperatura,
                    "filename": image_file.filename,
                    "image_size": len(image_data_bytes),
                    "image_url": image_url,
                    "json_url": json_url,
                    "batch_type": "image_recepcion",
                }
            )

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
