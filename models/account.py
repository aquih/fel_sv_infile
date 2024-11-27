# -*- encoding: utf-8 -*-

from odoo import models, fields, api, tools, _
from odoo.exceptions import UserError, ValidationError

import requests
import logging
import json

class AccountMove(models.Model):
    _inherit = "account.move"

    pdf_fel_sv = fields.Char('PDF FEL SV', copy=False)

    def _post(self, soft=True):
        if self.certificar_sv():
            return super(AccountMove, self)._post(soft)

    def post(self):
        if self.certificar_sv():
            return super(AccountMove, self).post()
    
    def formato_float(self, valor, redondeo):
        return float('{:.6f}'.format(tools.float_round(valor, precision_digits=redondeo)))

    def certificar_sv(self):
        for factura in self:
            if factura.requiere_certificacion_sv('infile_sv'):
                self.ensure_one()

                if factura.error_pre_validacion_sv():
                    return False

                tipo_documento = factura.journal_id.tipo_documento_fel_sv.zfill(2)
            
                factura_json = { 'documento': {
                    'tipo_dte': tipo_documento,
                    'establecimiento': factura.journal_id.codigo_establecimiento_sv,
                }}

                condicion_pago_fel_sv = factura.condicion_pago_fel_sv or factura.journal_id.condicion_pago_fel_sv
                forma_pago_fel_sv = factura.forma_pago_fel_sv or factura.journal_id.forma_pago_fel_sv
                
                incluir_impuestos = True
                if tipo_documento == '01':
                    factura_json['documento']['condicion_pago'] = int(condicion_pago_fel_sv)
                    if condicion_pago_fel_sv == '1':
                        factura_json['documento']['pagos'] = [{ 'tipo': forma_pago_fel_sv, 'monto': self.formato_float(factura.amount_total, 4) }]

                    receptor = {
                        'nombre': factura.partner_id.name,
                        'correo': factura.partner_id.email,
                    }
                    factura_json['documento']['receptor'] = receptor

                if tipo_documento in ['03', '04', '05', '06', '11', '14']:
                    incluir_impuestos = False
                    if tipo_documento in ['11', '14']:
                        incluir_impuestos = True

                    factura_json['documento']['condicion_pago'] = int(condicion_pago_fel_sv)
                    if condicion_pago_fel_sv == '1':
                        factura_json['documento']['pagos'] = [{ 'tipo': forma_pago_fel_sv, 'monto': self.formato_float(factura.amount_total, 4) }]

                    receptor = {
                        'tipo_documento': factura.partner_id.tipo_documento_fel_sv,
                        'numero_documento': factura.partner_id.vat,
                        'nrc': factura.partner_id.numero_registro,
                        'nombre': factura.partner_id.name,
                        'codigo_actividad': factura.partner_id.giro_negocio_id.codigo,
                        'nombre_comercial': factura.partner_id.nombre_comercial_fel_sv,
                        'correo': factura.partner_id.email,
                        'direccion': {
                            'departamento': factura.partner_id.departamento_fel_sv,
                            'municipio': factura.partner_id.municipio_fel_sv,
                            'complemento': factura.partner_id.street or '',
                        },
                        'telefono': factura.partner_id.phone,
                    }
                    
                    llave_receptor = 'receptor'
                    if tipo_documento in ['14']:
                        llave_receptor = 'sujeto_excluido'

                    factura_json['documento'][llave_receptor] = receptor

                    if tipo_documento in ['11']:
                        factura_json['documento']['tipo_item_exportacion'] = 2
                        factura_json['documento'][llave_receptor]['tipo_persona'] = 2
                        factura_json['documento'][llave_receptor]['codigo_pais'] = factura.partner_id.country_id.codigo_fel_sv or ''
                        factura_json['documento'][llave_receptor]['descripcion_actividad'] = factura.partner_id.descripcion_actividad_fel_sv or ''
                        factura_json['documento'][llave_receptor]['complemento'] = factura.partner_id.street or ''
                        del factura_json['documento'][llave_receptor]['direccion']
                        

                    if tipo_documento in ['05', '06']:
                        factura_json['documento']['documentos_relacionados'] = [{
                            'tipo_documento': factura.factura_original_fel_sv_id.journal_id.tipo_documento_fel_sv.zfill(2),
                            'tipo_generacion': 2,
                            'numero_documento': factura.factura_original_fel_sv_id.firma_fel_sv,
                            'fecha_emision': str(factura.factura_original_fel_sv_id.invoice_date),
                        }]
                    
                retenciones = 0
                items = [];
                for linea in factura.invoice_line_ids:
                    # El precio unitario no debe llevar IVA
                    r = linea.tax_ids.compute_all(linea.price_unit, currency=factura.currency_id, quantity=1, product=linea.product_id, partner=factura.partner_id)
                    precio_unitario = r['total_excluded']
                    impuestos = sum([t['amount'] for t in r['taxes'] if t['amount'] > 0])
                    if incluir_impuestos:
                        precio_unitario += impuestos

                    # Para calcular los impuestos, se debe quitar el descuento (price_subtotal)
                    r = linea.tax_ids.compute_all(linea.price_subtotal, currency=factura.currency_id, quantity=1, product=linea.product_id, partner=factura.partner_id)
                    if sum([t['amount'] for t in r['taxes'] if t['amount'] < 0]):
                        retenciones += r['total_excluded'] - r['total_included']
                           
                    item = {
                        'tipo': 1 if linea.product_id.type != 'service' else 2,
                        'cantidad': float('{:.6f}'.format(linea.quantity)),
                        'unidad_medida': int(linea.product_id.codigo_unidad_medida_fel_sv) or 59,
                        'descuento': self.formato_float((precio_unitario * linea.quantity) * (linea.discount / 100), 4),
                        'descripcion': linea.name,
                        'precio_unitario': self.formato_float(precio_unitario, 4),
                    }
                    if impuestos == 0:
                        item['tipo_venta'] = '3'

                    if tipo_documento in ['05', '06']:
                        item['numero_documento'] = factura.factura_original_fel_sv_id.firma_fel_sv
                    if not incluir_impuestos:
                        item['tributos'] = [{ 'codigo': '20', 'monto': self.formato_float(impuestos, 4) }]
                        
                    items.append(item)
                
                if retenciones != 0:
                    factura_json['documento']['retener_iva'] = True
                    if tipo_documento in ['14']:
                        factura_json['documento']['renta_retenida'] = self.formato_float(retenciones, 4)
                factura_json['documento']['items'] = items

                logging.warning(json.dumps(factura_json))                

                headers = {
                    "Content-Type": "application/json",
                    "usuario": factura.company_id.usuario_fel_sv,
                    "llave": factura.company_id.llave_fel_sv,
                    "identificador": factura.journal_id.code+str(factura.id),
                }
                logging.warning(headers)
                url = 'https://sandbox-certificador.infile.com.sv/api/v1/certificacion/test/documento/certificar' 
                if factura.company_id.pruebas_fel_sv:
                    url = 'https://certificador.infile.com.sv/api/v1/certificacion/test/documento/certificar'
                r = requests.post(url, json=factura_json, headers=headers)

                logging.warning(r.text)
                certificacion_json = r.json()
                if certificacion_json["ok"]:
                    factura.firma_fel_sv = certificacion_json["respuesta"]["codigoGeneracion"]
                    factura.pdf_fel_sv = certificacion_json["pdf_path"]
                    factura.certificador_fel_sv = "infile_sv"
                else:
                    factura.error_certificador_sv(str(certificacion_json["errores"]))
                    return False

        return True
        
    def button_cancel(self):
        result = super(AccountMove, self).button_cancel()
        for factura in self:
            if factura.requiere_certificacion_sv('infile_sv') and factura.firma_fel_sv:
                                    
                invalidacion_json = { 'invalidacion': {
                    'establecimiento': factura.journal_id.codigo_establecimiento_sv,
                    'uuid': factura.firma_fel_sv,
                    'tipo_anulacion': int(factura.tipo_anulacion_fel_sv),
                    'motivo': factura.motivo_fel_sv,
                    'responsable': {
                        'nombre': factura.responsable_fel_sv_id.name,
                        'tipo_documento': factura.responsable_fel_sv_id.tipo_documento_fel_sv,
                        'numero_documento': factura.responsable_fel_sv_id.vat,
                    },
                    'solicitante': {
                        'nombre': factura.solicitante_fel_sv_id.name,
                        'tipo_documento': factura.solicitante_fel_sv_id.tipo_documento_fel_sv,
                        'numero_documento': factura.solicitante_fel_sv_id.vat,
                        'correo': factura.solicitante_fel_sv_id.email
                    }
                }}

                logging.warning(json.dumps(invalidacion_json))                

                headers = {
                    "Content-Type": "application/json",
                    "usuario": factura.company_id.usuario_fel_sv,
                    "llave": factura.company_id.llave_fel_sv,
                }
                url = 'https://sandbox-certificador.infile.com.sv/api/v1/certificacion/test/documento/invalidacion'
                if factura.company_id.pruebas_fel_sv:
                    url = 'https://certificador.infile.com.sv/api/v1/certificacion/test/documento/invalidacion'
                r = requests.post(url, json=invalidacion_json, headers=headers)

                logging.warning(r.text)
                certificacion_json = r.json()
                if not certificacion_json["ok"]:
                    raise UserError(str(certificacion_json["mensaje"]))

class ResCompany(models.Model):
    _inherit = "res.company"

    usuario_fel_sv = fields.Char('Usuario FEL SV')
    llave_fel_sv = fields.Char('Clave FEL SV')
    certificador_fel_sv = fields.Selection(selection_add=[('infile_sv', 'Infile SV')])
    pruebas_fel_sv = fields.Boolean('Pruebas FEL SV')
