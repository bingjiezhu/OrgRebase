package main

import (
	"bytes"
	"errors"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

type jsonKind uint8

const (
	kindNull jsonKind = iota
	kindBool
	kindString
	kindNumber
	kindArray
	kindObject
)

type jsonValue struct {
	kind   jsonKind
	bool   bool
	string string
	number float64
	array  []jsonValue
	object []jsonMember
}

type jsonMember struct {
	key   string
	value jsonValue
}

type jsonDomainError struct{ message string }

func (e *jsonDomainError) Error() string { return e.message }

type jsonDepthError struct{}

func (e *jsonDepthError) Error() string { return "JSON value exceeds maxJsonDepth" }

type jsonParser struct {
	data  []byte
	pos   int
	depth int
}

func parseJSON(data []byte) (jsonValue, error) {
	if !utf8.Valid(data) {
		return jsonValue{}, &jsonDomainError{message: "invalid UTF-8"}
	}
	parser := jsonParser{data: data}
	parser.skipWhitespace()
	value, err := parser.parseValue()
	if err != nil {
		return jsonValue{}, err
	}
	parser.skipWhitespace()
	if parser.pos != len(parser.data) {
		return jsonValue{}, errors.New("trailing JSON data")
	}
	return value, nil
}

func canonicalizeJSON(data []byte) ([]byte, error) {
	value, err := parseJSON(data)
	if err != nil {
		return nil, err
	}
	var output bytes.Buffer
	appendCanonical(&output, value)
	return output.Bytes(), nil
}

func (p *jsonParser) parseValue() (jsonValue, error) {
	p.depth++
	defer func() { p.depth-- }()
	if p.depth > 64 {
		return jsonValue{}, &jsonDepthError{}
	}
	if p.pos >= len(p.data) {
		return jsonValue{}, errors.New("unexpected end of JSON")
	}
	switch p.data[p.pos] {
	case 'n':
		if !p.consumeLiteral("null") {
			return jsonValue{}, errors.New("invalid JSON literal")
		}
		return jsonValue{kind: kindNull}, nil
	case 't':
		if !p.consumeLiteral("true") {
			return jsonValue{}, errors.New("invalid JSON literal")
		}
		return jsonValue{kind: kindBool, bool: true}, nil
	case 'f':
		if !p.consumeLiteral("false") {
			return jsonValue{}, errors.New("invalid JSON literal")
		}
		return jsonValue{kind: kindBool}, nil
	case '"':
		value, err := p.parseString()
		return jsonValue{kind: kindString, string: value}, err
	case '[':
		return p.parseArray()
	case '{':
		return p.parseObject()
	default:
		if p.data[p.pos] == '-' || (p.data[p.pos] >= '0' && p.data[p.pos] <= '9') {
			return p.parseNumber()
		}
		return jsonValue{}, errors.New("invalid JSON value")
	}
}

func (p *jsonParser) parseArray() (jsonValue, error) {
	p.pos++
	p.skipWhitespace()
	values := make([]jsonValue, 0)
	if p.take(']') {
		return jsonValue{kind: kindArray, array: values}, nil
	}
	for {
		value, err := p.parseValue()
		if err != nil {
			return jsonValue{}, err
		}
		values = append(values, value)
		p.skipWhitespace()
		if p.take(']') {
			return jsonValue{kind: kindArray, array: values}, nil
		}
		if !p.take(',') {
			return jsonValue{}, errors.New("invalid JSON array")
		}
		p.skipWhitespace()
	}
}

func (p *jsonParser) parseObject() (jsonValue, error) {
	p.pos++
	p.skipWhitespace()
	members := make([]jsonMember, 0)
	seen := make(map[string]struct{})
	if p.take('}') {
		return jsonValue{kind: kindObject, object: members}, nil
	}
	for {
		if p.pos >= len(p.data) || p.data[p.pos] != '"' {
			return jsonValue{}, errors.New("invalid JSON object key")
		}
		key, err := p.parseString()
		if err != nil {
			return jsonValue{}, err
		}
		if _, duplicate := seen[key]; duplicate {
			return jsonValue{}, errors.New("duplicate JSON object key")
		}
		seen[key] = struct{}{}
		p.skipWhitespace()
		if !p.take(':') {
			return jsonValue{}, errors.New("missing JSON object colon")
		}
		p.skipWhitespace()
		value, err := p.parseValue()
		if err != nil {
			return jsonValue{}, err
		}
		members = append(members, jsonMember{key: key, value: value})
		p.skipWhitespace()
		if p.take('}') {
			return jsonValue{kind: kindObject, object: members}, nil
		}
		if !p.take(',') {
			return jsonValue{}, errors.New("invalid JSON object")
		}
		p.skipWhitespace()
	}
}

func (p *jsonParser) parseString() (string, error) {
	p.pos++
	var output strings.Builder
	for p.pos < len(p.data) {
		current := p.data[p.pos]
		if current == '"' {
			p.pos++
			return output.String(), nil
		}
		if current < 0x20 {
			return "", errors.New("unescaped control in JSON string")
		}
		if current == '\\' {
			p.pos++
			if p.pos >= len(p.data) {
				return "", errors.New("truncated JSON escape")
			}
			escape := p.data[p.pos]
			p.pos++
			switch escape {
			case '"', '\\', '/':
				output.WriteByte(escape)
			case 'b':
				output.WriteByte('\b')
			case 'f':
				output.WriteByte('\f')
			case 'n':
				output.WriteByte('\n')
			case 'r':
				output.WriteByte('\r')
			case 't':
				output.WriteByte('\t')
			case 'u':
				code, err := p.parseHex4()
				if err != nil {
					return "", err
				}
				if code >= 0xD800 && code <= 0xDBFF {
					if p.pos+2 > len(p.data) || p.data[p.pos] != '\\' || p.data[p.pos+1] != 'u' {
						return "", &jsonDomainError{message: "unpaired high surrogate"}
					}
					p.pos += 2
					low, err := p.parseHex4()
					if err != nil {
						return "", err
					}
					if low < 0xDC00 || low > 0xDFFF {
						return "", &jsonDomainError{message: "unpaired high surrogate"}
					}
					output.WriteRune(utf16.DecodeRune(rune(code), rune(low)))
				} else if code >= 0xDC00 && code <= 0xDFFF {
					return "", &jsonDomainError{message: "unpaired low surrogate"}
				} else {
					output.WriteRune(rune(code))
				}
			default:
				return "", errors.New("invalid JSON escape")
			}
			continue
		}
		if current < utf8.RuneSelf {
			output.WriteByte(current)
			p.pos++
			continue
		}
		r, size := utf8.DecodeRune(p.data[p.pos:])
		output.WriteRune(r)
		p.pos += size
	}
	return "", errors.New("unterminated JSON string")
}

func (p *jsonParser) parseHex4() (uint16, error) {
	if p.pos+4 > len(p.data) {
		return 0, errors.New("truncated Unicode escape")
	}
	var value uint16
	for count := 0; count < 4; count++ {
		nibble, ok := hexNibble(p.data[p.pos+count])
		if !ok {
			return 0, errors.New("invalid Unicode escape")
		}
		value = value<<4 | uint16(nibble)
	}
	p.pos += 4
	return value, nil
}

func (p *jsonParser) parseNumber() (jsonValue, error) {
	start := p.pos
	if p.take('-') && p.pos >= len(p.data) {
		return jsonValue{}, errors.New("invalid JSON number")
	}
	if p.take('0') {
		if p.pos < len(p.data) && p.data[p.pos] >= '0' && p.data[p.pos] <= '9' {
			return jsonValue{}, errors.New("leading zero in JSON number")
		}
	} else {
		if p.pos >= len(p.data) || p.data[p.pos] < '1' || p.data[p.pos] > '9' {
			return jsonValue{}, errors.New("invalid JSON number")
		}
		for p.pos < len(p.data) && p.data[p.pos] >= '0' && p.data[p.pos] <= '9' {
			p.pos++
		}
	}
	if p.take('.') {
		if p.pos >= len(p.data) || p.data[p.pos] < '0' || p.data[p.pos] > '9' {
			return jsonValue{}, errors.New("invalid JSON fraction")
		}
		for p.pos < len(p.data) && p.data[p.pos] >= '0' && p.data[p.pos] <= '9' {
			p.pos++
		}
	}
	if p.pos < len(p.data) && (p.data[p.pos] == 'e' || p.data[p.pos] == 'E') {
		p.pos++
		if p.pos < len(p.data) && (p.data[p.pos] == '+' || p.data[p.pos] == '-') {
			p.pos++
		}
		if p.pos >= len(p.data) || p.data[p.pos] < '0' || p.data[p.pos] > '9' {
			return jsonValue{}, errors.New("invalid JSON exponent")
		}
		for p.pos < len(p.data) && p.data[p.pos] >= '0' && p.data[p.pos] <= '9' {
			p.pos++
		}
	}
	raw := string(p.data[start:p.pos])
	number, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsInf(number, 0) || math.IsNaN(number) {
		return jsonValue{}, &jsonDomainError{message: "number outside IEEE-754 domain"}
	}
	return jsonValue{kind: kindNumber, number: number}, nil
}

func (p *jsonParser) consumeLiteral(literal string) bool {
	if len(p.data)-p.pos < len(literal) || string(p.data[p.pos:p.pos+len(literal)]) != literal {
		return false
	}
	p.pos += len(literal)
	return true
}

func (p *jsonParser) skipWhitespace() {
	for p.pos < len(p.data) {
		switch p.data[p.pos] {
		case ' ', '\t', '\n', '\r':
			p.pos++
		default:
			return
		}
	}
}

func (p *jsonParser) take(expected byte) bool {
	if p.pos < len(p.data) && p.data[p.pos] == expected {
		p.pos++
		return true
	}
	return false
}

func appendCanonical(output *bytes.Buffer, value jsonValue) {
	switch value.kind {
	case kindNull:
		output.WriteString("null")
	case kindBool:
		if value.bool {
			output.WriteString("true")
		} else {
			output.WriteString("false")
		}
	case kindString:
		appendCanonicalString(output, value.string)
	case kindNumber:
		output.WriteString(formatECMAScriptNumber(value.number))
	case kindArray:
		output.WriteByte('[')
		for index, item := range value.array {
			if index > 0 {
				output.WriteByte(',')
			}
			appendCanonical(output, item)
		}
		output.WriteByte(']')
	case kindObject:
		members := append([]jsonMember(nil), value.object...)
		sort.Slice(members, func(i, j int) bool { return utf16Less(members[i].key, members[j].key) })
		output.WriteByte('{')
		for index, member := range members {
			if index > 0 {
				output.WriteByte(',')
			}
			appendCanonicalString(output, member.key)
			output.WriteByte(':')
			appendCanonical(output, member.value)
		}
		output.WriteByte('}')
	}
}

func appendCanonicalString(output *bytes.Buffer, value string) {
	output.WriteByte('"')
	for _, r := range value {
		switch r {
		case '"':
			output.WriteString("\\\"")
		case '\\':
			output.WriteString("\\\\")
		case '\b':
			output.WriteString("\\b")
		case '\t':
			output.WriteString("\\t")
		case '\n':
			output.WriteString("\\n")
		case '\f':
			output.WriteString("\\f")
		case '\r':
			output.WriteString("\\r")
		default:
			if r < 0x20 {
				const hex = "0123456789abcdef"
				output.WriteString("\\u00")
				output.WriteByte(hex[byte(r)>>4])
				output.WriteByte(hex[byte(r)&0x0f])
			} else {
				output.WriteRune(r)
			}
		}
	}
	output.WriteByte('"')
}

func formatECMAScriptNumber(value float64) string {
	if value == 0 {
		return "0"
	}
	prefix := ""
	if value < 0 {
		prefix = "-"
		value = -value
	}
	exponential := strconv.FormatFloat(value, 'e', -1, 64)
	separator := strings.IndexByte(exponential, 'e')
	mantissa := exponential[:separator]
	exponent, _ := strconv.Atoi(exponential[separator+1:])
	digits := strings.ReplaceAll(mantissa, ".", "")
	k := len(digits)
	n := exponent + 1

	switch {
	case k <= n && n <= 21:
		return prefix + digits + strings.Repeat("0", n-k)
	case 0 < n && n <= 21:
		return prefix + digits[:n] + "." + digits[n:]
	case -6 < n && n <= 0:
		return prefix + "0." + strings.Repeat("0", -n) + digits
	default:
		coefficient := digits[:1]
		if len(digits) > 1 {
			coefficient += "." + digits[1:]
		}
		scientificExponent := n - 1
		sign := ""
		if scientificExponent >= 0 {
			sign = "+"
		}
		return prefix + coefficient + "e" + sign + strconv.Itoa(scientificExponent)
	}
}

func utf16Less(left, right string) bool {
	a := utf16.Encode([]rune(left))
	b := utf16.Encode([]rune(right))
	limit := len(a)
	if len(b) < limit {
		limit = len(b)
	}
	for index := 0; index < limit; index++ {
		if a[index] != b[index] {
			return a[index] < b[index]
		}
	}
	return len(a) < len(b)
}

func hexNibble(value byte) (byte, bool) {
	switch {
	case value >= '0' && value <= '9':
		return value - '0', true
	case value >= 'a' && value <= 'f':
		return value - 'a' + 10, true
	case value >= 'A' && value <= 'F':
		return value - 'A' + 10, true
	default:
		return 0, false
	}
}

func jsonObject(members ...jsonMember) jsonValue {
	return jsonValue{kind: kindObject, object: members}
}

func jsonArrayStrings(values []string) jsonValue {
	items := make([]jsonValue, len(values))
	for index, value := range values {
		items[index] = jsonValue{kind: kindString, string: value}
	}
	return jsonValue{kind: kindArray, array: items}
}

func jsonString(value string) jsonValue { return jsonValue{kind: kindString, string: value} }
func jsonBool(value bool) jsonValue     { return jsonValue{kind: kindBool, bool: value} }
func jsonNull() jsonValue               { return jsonValue{kind: kindNull} }

func canonicalBytes(value jsonValue) []byte {
	var output bytes.Buffer
	appendCanonical(&output, value)
	return output.Bytes()
}
