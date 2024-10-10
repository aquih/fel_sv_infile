# -*- encoding: utf-8 -*-

from odoo import models, fields, api, tools, _
from odoo.exceptions import UserError, ValidationError

import base64
import requests

import logging
import json

class AccountInvoice(models.Model):
    _inherit = "account.invoice"

    pdf_fel_sv = fields.Char('PDF FEL SV', copy=False)
    
    def invoice_validate(self):
        if self.certificar_sv():
            return super(AccountInvoice, self).invoice_validate()

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
                    }
                    factura_json['documento']['receptor'] = receptor

                if tipo_documento in ['03', '04']:
                    incluir_impuestos = False
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
                    factura_json['documento']['receptor'] = receptor

                items = [];
                for linea in factura.invoice_line_ids:
                    precio_unitario = linea.price_unit
                    impuestos = 0
                    if not incluir_impuestos and len(linea.invoice_line_tax_ids) > 0:
                        r = linea.invoice_line_tax_ids.compute_all(linea.price_unit, currency=factura.currency_id, quantity=1, product=linea.product_id, partner=factura.partner_id)
                        precio_unitario = r['base']

                        # Para calcular los impuestos, es necesario quitar el descuento y tomar en cuenta todas las cantidades
                        r = linea.invoice_line_tax_ids.compute_all(linea.price_total, currency=factura.currency_id, quantity=1, product=linea.product_id, partner=factura.partner_id)
                        impuestos = r['total_included'] - r['base']
                           
                    item = {
                        'tipo': 1 if linea.product_id.type != 'service' else 2,
                        'cantidad': float('{:.6f}'.format(linea.quantity)),
                        'unidad_medida': int(linea.product_id.codigo_unidad_medida_fel_sv) or 59,
                        'descuento': self.formato_float((precio_unitario * linea.quantity) - linea.price_subtotal, 4),
                        'descripcion': linea.name,
                        'precio_unitario': self.formato_float(precio_unitario, 4),
                    }
                    if not incluir_impuestos:
                        item['tributos'] = [{ 'codigo': '20', 'monto': self.formato_float(impuestos, 4) }]
                        
                    items.append(item)
                
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
        
    def action_cancel(self):
        result = super(AccountInvoice, self).action_cancel()
        for factura in self:
            if factura.requiere_certificacion_sv('infile_sv') and factura.firma_fel_sv:
                                    
                invalidacion_json = { 'documento': {
                    'establecimiento': factura.journal_id.codigo_establecimiento_sv,
                    'uuid': factura.journal_id.firma_fel_sv,
                    'tipo_anulacion': factura.tipo_anulacion_fel_sv,
                    'motivo': factura.motivo_fel_sv,
                    'nuevo_documento': factura.factura_nueva_fel_sv_id.firma_fel_sv,
                    'responsable': {
                        'nombre': factura.responsable_fel_sv_id.name,
                        'tipo_documento': factura.responsable_fel_sv_id.tipo_documento_fel_sv,
                        'numero_documento': factura.responsable_fel_sv_id.vat,
                    },
                    'solicitante': {
                        'nombre': factura.solicitante_fel_sv_id.name,
                        'tipo_documento': factura.solicitante_fel_sv_id.tipo_documento_fel_sv,
                        'numero_documento': factura.solicitante_fel_sv_id.vat,
                    }
                }}

                condicion_pago_fel_sv = factura.condicion_pago_fel_sv or factura.journal_id.condicion_pago_fel_sv
                
                if tipo_documento == '01':
                    invalidacion_json['documento']['condicion_pago'] = int(condicion_pago_fel_sv)

                if tipo_documento == '03':
                    invalidacion_json['documento']['condicion_pago'] = int(condicion_pago_fel_sv)
                    receptor = {
                        'tipo_documento': factura.partner_id.tipo_documento_fel_sv,
                        'numero_documento': factura.partner_id.vat,
                        'nrc': factura.partner_id.numero_registro,
                        'nombre': factura.partner_id.name,
                        'codigo_actividad': factura.partner_id.giro_negocio_id.codigo,
                        'nombre_comercial': factura.partner_id.nombre_comercial_fel_sv,
                        'direccion': {
                            'departamento': factura.partner_id.departamento_fel_sv,
                            'municipio': factura.partner_id.municipio_fel_sv,
                            'complemento': factura.partner_id.street or '',
                        }
                    }
                    invalidacion_json['documento']['receptor'] = receptor

                items = [];
                for linea in factura.invoice_line_ids:
                    item = {
                        'tipo': 1 if linea.product_id.type != 'service' else 2,
                        'cantidad': linea.quantity,
                        'unidad_medida': linea.product_id.codigo_unidad_medida_fel_sv or 59,
                        'descuento': linea.price_total * linea.discount / 100.0,
                        'descripcion': linea.name,
                        'precio_unitario': linea.price_unit,
                    }
                
                logging.warning(invalidacion_json)                

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
                    raise UserError(str(certificacion_json["descripcion_errores"]))

class ResCompany(models.Model):
    _inherit = "res.company"

    usuario_fel_sv = fields.Char('Usuario FEL SV')
    llave_fel_sv = fields.Char('Clave FEL SV')
    certificador_fel_sv = fields.Selection(selection_add=[('infile_sv', 'Infile SV')])
    pruebas_fel_sv = fields.Boolean('Pruebas FEL SV')

class PosOrder(models.Model):
    _inherit = "pos.order"

    firma_fel_sv = fields.Char('Firma FEL SV', related='invoice_id.firma_fel_sv')
    pdf_fel_sv = fields.Char('PDF FEL SV', related='invoice_id.pdf_fel_sv')
